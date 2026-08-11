#!/usr/bin/env python3
"""Scaffold, extract, inventory, validate, and merge low-token book companion projects.

Commands:
  init       Create a new book project directory with AGENTS.md and manifest template.
  extract    Extract plain text from PDF/EPUB/MOBI/TXT into source_text/.
  inventory  Scan extracted text and produce a structured unit manifest summary.
  status     Show current project progress counts and problems.
  validate   Check all completed units for file existence, non-empty, and content rules.
  merge      Concatenate all completed reader files into final/book-companion.md.
"""

import argparse
import json
import re
import shutil
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

def min_reader_chars(source_chars):
    """Dynamic minimum: proportional for short chapters, floor for medium, scaled for long."""
    if source_chars < 5000:
        return max(1000, int(source_chars * 0.5))
    elif source_chars <= 20000:
        return 3000
    else:
        return min(8000, 3000 + int((source_chars - 20000) * 0.10))


def max_reader_chars(source_chars):
    """Soft ceiling: reader must compress the source (<= 80% of source chars).

    Floored at 1.6x the minimum so short chapters never get a ceiling below
    their floor. Exceeding this means near-verbatim transcription, not summary.
    """
    return max(int(source_chars * 0.8), int(min_reader_chars(source_chars) * 1.6))


def source_cn_chars(project: Path, unit):
    """Chinese-char count of the unit's actual source text, or None if unavailable."""
    sf = unit.get("source_file")
    if sf:
        p = project / sf
        if p.is_file():
            t = p.read_text(encoding="utf-8", errors="replace")
            return sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
    return None

BAD_READER_TEXT = (
    "注释与原文出处",
    "原文明确陈述",
    "source_text/",
    "source_text/chapters/",
    "![",
)


def load_manifest(project: Path):
    path = project / "manifest.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_manifest(project: Path, data):
    (project / "manifest.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def unit_paths(project: Path, unit):
    reader = project / unit.get("reader", f"reader/{unit['id']}.md")
    evidence = project / unit.get("evidence", f"evidence/{unit['id']}.md")
    return reader, evidence


# =============================================================================
#  Extract
# =============================================================================

def extract_pdf(book_path, output_dir):
    import pdfplumber
    output_file = output_dir / "full_text.txt"
    total_chars = 0
    page_count = 0
    pages_out = []
    with pdfplumber.open(book_path) as pdf:
        page_count = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text:
                pages_out.append(f"--- PDF_PAGE_{i:04d} ---")
                pages_out.append(text.strip())
                total_chars += len(text)
    full_text = "\n\n".join(pages_out) + "\n"
    output_file.write_text(full_text, encoding="utf-8")
    # Custom-font PDFs (common in Z-Library scans) yield "(cid:…)" placeholders
    # instead of readable text. Warn loudly so the operator can switch to OCR.
    cid_hits = full_text.count("(cid:")
    if cid_hits > 100:
        print(f"  WARNING: found {cid_hits} '(cid:…)' glyph placeholders. "
              f"This PDF likely uses custom font encoding and pdfplumber output "
              f"is probably unusable. Consider OCR instead (e.g., macOS Vision "
              f"via VNRecognizeTextRequest).", file=sys.stderr)
    return {"type": "pdf", "pages": page_count, "total_chars": total_chars,
            "cid_placeholders": cid_hits}


def strip_html(html_text):
    class _Extractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self._skip = 0
        def handle_starttag(self, tag, _attrs):
            if tag in ("script", "style", "head", "title", "metadata"):
                self._skip += 1
        def handle_endtag(self, tag):
            if tag in ("script", "style", "head", "title", "metadata") and self._skip > 0:
                self._skip -= 1
            if tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
                       "li", "tr", "section", "article", "blockquote"):
                self.parts.append("\n")
        def handle_data(self, data):
            if self._skip == 0:
                self.parts.append(data)
    ex = _Extractor()
    ex.feed(html_text)
    text = "".join(ex.parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_epub(book_path, output_dir):
    chapters_dir = output_dir / "chapters"
    chapters_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(book_path) as zf:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        ns_c = "urn:oasis:names:tc:opendocument:xmlns:container"
        rootfile = container.find(f"{{{ns_c}}}rootfiles/{{{ns_c}}}rootfile")
        opf_path = rootfile.get("full-path") if rootfile is not None else None
        if not opf_path:
            for candidate in ("content.opf", "OEBPS/content.opf", "OPS/content.opf"):
                try:
                    zf.getinfo(candidate)
                    opf_path = candidate
                    break
                except KeyError:
                    continue
        if not opf_path:
            raise FileNotFoundError("Cannot locate OPF inside EPUB")
        opf_xml = zf.read(opf_path)
        opf = ET.fromstring(opf_xml)
        opf_ns = opf.tag.rpartition("}")[0] + "}" if "}" in opf.tag else ""
        id_to_href = {}
        for item in opf.findall(f"{opf_ns}manifest/{opf_ns}item"):
            id_to_href[item.get("id")] = item.get("href")
        opf_parent = Path(opf_path).parent
        spine_files = []
        spine = opf.find(f"{opf_ns}spine")
        if spine is not None:
            for itemref in spine.findall(f"{opf_ns}itemref"):
                href = id_to_href.get(itemref.get("idref"))
                if href:
                    spine_files.append(href)
        else:
            for item in opf.findall(f"{opf_ns}manifest/{opf_ns}item"):
                mt = (item.get("media-type") or "").lower()
                href = item.get("href")
                if href and ("xhtml" in mt or "html" in mt):
                    spine_files.append(href)
        chapter_files = []
        total_chars = 0
        for i, href in enumerate(spine_files, 1):
            raw = str(opf_parent / href)
            html = None
            for candidate in (raw, unquote(raw)):
                try:
                    html = zf.read(candidate).decode("utf-8", errors="replace")
                    break
                except KeyError:
                    continue
            if html is None:
                continue
            text = strip_html(html)
            if not text:
                continue
            stem = Path(href).stem
            out_name = f"{i:03d}_{stem}.txt"
            out_path = chapters_dir / out_name
            out_path.write_text(text + "\n", encoding="utf-8")
            chapter_files.append(out_name)
            total_chars += len(text)
    index_path = output_dir / "chapter_index.txt"
    with index_path.open("w", encoding="utf-8") as f:
        f.write(f"source: {book_path.name}\nchapters: {len(chapter_files)}\n\n")
        for cf in chapter_files:
            f.write(f"{cf}\n")
    return {"type": "epub", "chapter_count": len(chapter_files), "total_chars": total_chars}


def extract_mobi(book_path, output_dir):
    ebook_convert = shutil.which("ebook-convert")
    if ebook_convert:
        import subprocess
        epub_tmp = output_dir / "_temp_mobi.epub"
        subprocess.run([ebook_convert, str(book_path), str(epub_tmp)], check=True)
        try:
            result = extract_epub(epub_tmp, output_dir)
        finally:
            if epub_tmp.exists():
                epub_tmp.unlink()
        return result
    raise RuntimeError(
        "MOBI/AZW extraction requires ebook-convert (Calibre). "
        "Install Calibre or convert the file to EPUB first, then re-run with the EPUB."
    )


def cmd_extract(args):
    project = Path(args.project).expanduser().resolve()
    book_path = Path(args.book).expanduser().resolve()
    if not book_path.is_file():
        print(f"ERROR: book file not found: {book_path}", file=sys.stderr)
        return 1
    source_dir = project / "source_text"
    source_dir.mkdir(parents=True, exist_ok=True)
    fmt = book_path.suffix.lower()
    if fmt == ".pdf":
        info = extract_pdf(book_path, source_dir)
    elif fmt == ".epub":
        info = extract_epub(book_path, source_dir)
    elif fmt in (".mobi", ".azw", ".azw3"):
        info = extract_mobi(book_path, source_dir)
    elif fmt == ".txt":
        shutil.copy2(book_path, source_dir / "full_text.txt")
        info = {"type": "plain_text", "total_chars": len(book_path.read_text(encoding="utf-8"))}
    else:
        print(f"ERROR: unsupported format '{fmt}'", file=sys.stderr)
        return 1
    manifest_path = project / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["book"]["source_file"] = str(book_path)
        data["book"]["format"] = fmt.lstrip(".").upper()
        data["book"]["total_chars"] = info["total_chars"]
        data["book"]["extraction"] = {
            "type": info["type"],
            **{k: v for k, v in info.items() if k != "type"},
        }
        data["status"] = "extracted"
        manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted: {info['type']}  |  {info['total_chars']} chars  |  {project}")
    if info["type"] == "pdf":
        print(f"  pages: {info['pages']}")
    elif info["type"] == "epub":
        print(f"  chapters: {info['chapter_count']}")
    return 0


# =============================================================================
#  Inventory
# =============================================================================

CHAPTER_PATTERNS = [
    re.compile(r"第[一二三四五六七八九十百千\d]+章"),
    re.compile(r"第[一二三四五六七八九十百千\d]+节"),
    re.compile(r"Chapter\s+\d+", re.IGNORECASE),
    re.compile(r"Part\s+\d+", re.IGNORECASE),
    re.compile(r"[Cc][Hh][Aa][Pp][Tt][Ee][Rr]\s+\d+"),
]


def _detect_chapter_starts(page_texts):
    starts = []
    for page_id, text in page_texts.items():
        head = text.strip()[:200]
        for pat in CHAPTER_PATTERNS:
            if pat.search(head):
                starts.append(page_id)
                break
    return starts


def cmd_inventory(args):
    project = Path(args.project).expanduser().resolve()
    source_dir = project / "source_text"
    data = load_manifest(project)
    chapters_dir = source_dir / "chapters"
    full_text_path = source_dir / "full_text.txt"
    if chapters_dir.is_dir() and any(chapters_dir.iterdir()):
        return _inventory_epub(project, chapters_dir, data)
    if full_text_path.is_file():
        return _inventory_pdf(project, full_text_path, data)
    print("ERROR: No extracted text found. Run 'extract' first.", file=sys.stderr)
    return 1


def _inventory_epub(project, chapters_dir, data):
    chapter_files = sorted(chapters_dir.glob("*.txt"))
    total_chars = 0
    units = []
    oversized = []
    for cf in chapter_files:
        text = cf.read_text(encoding="utf-8")
        char_count = len(text)
        total_chars += char_count
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        first_line = lines[0][:100] if lines else cf.stem
        unit_id = cf.stem
        unit = {
            "id": unit_id,
            "source_type": "epub_chapter",
            "source_file": f"source_text/chapters/{cf.name}",
            "char_count": char_count,
            "first_line": first_line,
            "status": "pending",
            "reader": f"reader/{unit_id}.md",
            "evidence": f"evidence/{unit_id}.md",
        }
        units.append(unit)
        if char_count > 55000:
            oversized.append(unit_id)
    data["units"] = units
    data["book"]["total_chars"] = total_chars
    data["status"] = "inventoried"
    save_manifest(project, data)
    print(f"source: EPUB  |  chapters: {len(units)}  |  total chars: {total_chars}\n")
    for u in units:
        flag = " ⚠ OVERSIZED" if u["id"] in oversized else ""
        print(f"  {u['id']:<35s} {u['char_count']:>7d} chars  |  {u['first_line']}{flag}")
    if oversized:
        print(f"\n⚠  {len(oversized)} chapter(s) exceed 25k chars. Split them before dispatch.")
    return 0


def _inventory_pdf(project, full_text_path, data):
    text = full_text_path.read_text(encoding="utf-8")
    pages = {}
    current_page = None
    buf = []
    for line in text.split("\n"):
        m = re.match(r"^--- (PDF_PAGE_\d{4}) ---$", line)
        if m:
            if current_page is not None:
                pages[current_page] = "\n".join(buf)
            current_page = m.group(1)
            buf = []
        else:
            buf.append(line)
    if current_page is not None:
        pages[current_page] = "\n".join(buf)
    total_chars = sum(len(t) for t in pages.values())
    page_list = sorted(pages.keys())
    chapter_candidates = _detect_chapter_starts(pages)
    interval = max(1, len(page_list) // 50)
    summary_lines = []
    for i, pid in enumerate(page_list):
        if i % interval == 0 or pid in chapter_candidates:
            preview = pages[pid].strip()[:100].replace("\n", " ")
            marker = " ← ch?" if pid in chapter_candidates else ""
            summary_lines.append(f"  {pid}  {len(pages[pid]):>5d} chars  {preview}{marker}")
    data["book"]["pages"] = {"total": len(pages), "chapter_candidates": chapter_candidates}
    data["book"]["total_chars"] = total_chars
    data["status"] = "inventoried"
    save_manifest(project, data)
    print(f"source: PDF  |  pages: {len(pages)}  |  total chars: {total_chars}")
    if chapter_candidates:
        print(f"potential chapters at: {', '.join(chapter_candidates[:15])}"
              f"{' …' if len(chapter_candidates) > 15 else ''}")
    print(f"\npage summary (1/{interval}):\n")
    print("\n".join(summary_lines))
    print(f"\n⚠  Define units manually in manifest.json with source_type 'pdf_range'.")
    return 0


# =============================================================================
#  Existing commands
# =============================================================================

def cmd_init(args):
    project = Path(args.project).expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    skill = Path(__file__).resolve().parent.parent
    for name in ("source_text", "reader", "evidence", "review", "final", "tmp"):
        (project / name).mkdir(exist_ok=True)
    agents = project / "AGENTS.md"
    manifest = project / "manifest.json"
    if not agents.exists():
        shutil.copy2(skill / "assets" / "AGENTS.md", agents)
    if not manifest.exists():
        data = json.loads((skill / "assets" / "manifest.template.json").read_text(encoding="utf-8"))
        if args.book:
            data["book"]["source_file"] = str(Path(args.book).expanduser().resolve())
            data["book"]["format"] = Path(args.book).suffix.lstrip(".").upper()
        save_manifest(project, data)
    print(project)


def inspect(project: Path):
    data = load_manifest(project)
    problems = []
    warnings = []
    counts = {}
    for unit in data.get("units", []):
        status = unit.get("status", "pending")
        counts[status] = counts.get(status, 0) + 1
        reader, evidence = unit_paths(project, unit)
        if status == "completed":
            for label, path in (("reader", reader), ("evidence", evidence)):
                if not path.is_file() or path.stat().st_size == 0:
                    problems.append(f"{unit['id']}: completed but {label} missing/empty: {path}")
            if reader.is_file():
                text = reader.read_text(encoding="utf-8", errors="replace")
                # Count Chinese characters (U+4E00 to U+9FFF)
                cn_chars = sum(1 for c in text if '一' <= c <= '鿿')
                # Prefer the real source file's CJK count so min and max share one base.
                src_cn = source_cn_chars(project, unit)
                required = None
                ceiling = None
                if src_cn:
                    required = min_reader_chars(src_cn)
                    ceiling = max_reader_chars(src_cn)
                else:
                    # Fallback 1: chunk sample size (lower bound only).
                    chunk_path = project / 'source_text' / 'chunks' / f'{unit["id"]}.txt'
                    if chunk_path.is_file():
                        chunk_text = chunk_path.read_text(encoding='utf-8', errors='replace')
                        chunk_cn = sum(1 for c in chunk_text if '一' <= c <= '鿿')
                        required = min_reader_chars(chunk_cn)
                    else:
                        # Fallback 2: manifest char_count.
                        stored_min = unit.get('_min_reader')
                        required = stored_min if stored_min else min_reader_chars(unit.get('char_count', 0))
                        if unit.get('char_count'):
                            ceiling = max_reader_chars(unit.get('char_count'))
                # Honor a floor stored at unit-definition time (never raise it silently).
                stored_min = unit.get('_min_reader')
                if stored_min and (required is None or stored_min > required):
                    required = stored_min
                if cn_chars < required:
                    problems.append(f"{unit['id']}: reader has only {cn_chars} Chinese chars (required {required})")
                # Soft upper bound: reader should be a summary, not a transcription
                if ceiling and cn_chars > ceiling:
                    base = src_cn or unit.get('char_count', 0)
                    pct = int(cn_chars / base * 100) if base else 0
                    warnings.append(
                        f"{unit['id']}: reader is {cn_chars} chars = {pct}% of source "
                        f"(soft cap {ceiling}); likely under-condensed, consider compressing"
                    )
                for bad in BAD_READER_TEXT:
                    if bad in text:
                        problems.append(f"{unit['id']}: reader contains forbidden text: {bad}")

    # Cross-file duplication check: flag files that share large identical blocks
    reader_files = {}
    for unit in data.get("units", []):
        if unit.get("status") == "completed":
            rp = project / unit.get("reader", f"reader/{unit['id']}.md")
            if rp.is_file():
                reader_files[unit["id"]] = rp.read_text(encoding="utf-8", errors="replace")
    
    uids = list(reader_files.keys())
    for i in range(len(uids)):
        for j in range(i+1, len(uids)):
            t1, t2 = reader_files[uids[i]], reader_files[uids[j]]
            # Find the longest common substring
            # Use a simple heuristic: check if any 200-char block appears in both
            for start in range(0, len(t1)-200, 100):
                block = t1[start:start+200]
                if block in t2 and len(block.strip()) > 50:
                    problems.append(f"CROSS-FILE DUPLICATION: {uids[i]} and {uids[j]} share identical content")
                    break
            else:
                continue
            break
    return data, counts, problems, warnings


def cmd_status(args):
    project = Path(args.project).expanduser().resolve()
    data, counts, problems, warnings = inspect(project)
    book = data.get("book", {})
    print(f"book: {book.get('title') or book.get('source_file', '')}")
    print(f"status: {data.get('status', 'unknown')}")
    print(f"units: {len(data.get('units', []))}")
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")
    if problems:
        print("problems:")
        print("\n".join(f"- {p}" for p in problems))
    if warnings:
        print("warnings:")
        print("\n".join(f"- {w}" for w in warnings))


def cmd_validate(args):
    project = Path(args.project).expanduser().resolve()
    data, counts, problems, warnings = inspect(project)
    ids = [u.get("id") for u in data.get("units", [])]
    if not ids:
        problems.append("manifest has no units")
    if len(ids) != len(set(ids)):
        problems.append("manifest contains duplicate unit ids")
    if problems:
        print("VALIDATION FAILED")
        print("\n".join(f"- {p}" for p in problems))
        return 1
    
    import subprocess as _sp
    vscript = Path(__file__).resolve().parent.parent / 'scripts' / 'verify_content.py'
    if not vscript.exists():
        vscript = project.parent / 'scripts' / 'verify_content.py'
        vscript = vscript.resolve() if hasattr(vscript, 'resolve') else vscript
    
    content_ok = True
    if vscript.exists():
        try:
            result = _sp.run([sys.executable, str(vscript), str(project)],
                           capture_output=True, text=True, timeout=30)
            print(result.stdout.strip())
            if result.returncode != 0:
                content_ok = False
        except Exception:
            pass
    
    print(f"VALIDATION PASSED: {len(ids)} units; statuses={counts}" + 
          (" (content warnings above)" if not content_ok else ""))
    if warnings:
        print(f"SOFT WARNINGS ({len(warnings)}): not blocking, but review")
        print("\n".join(f"- {w}" for w in warnings))
    return 0


def cmd_merge(args):
    import re
    project = Path(args.project).expanduser().resolve()
    data, _, problems, warnings = inspect(project)
    units = data.get("units", [])
    incomplete = [u.get("id") for u in units if u.get("status") != "completed"]
    if problems or incomplete or not units:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        if incomplete:
            print("ERROR: incomplete units: " + ", ".join(incomplete), file=sys.stderr)
        return 1
    parts = []
    book = data.get("book", {})
    title = book.get("title") or ""
    if not title:
        src = book.get("source_file", "")
        import os, re
        title = os.path.splitext(os.path.basename(src))[0] if src else "未命名"
        # Clean z-library junk: remove (author), (z-lib...), trailing spaces
        title = re.sub(r'\s*\([^)]*z-lib[^)]*\)', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s*\([^)]*1lib[^)]*\)', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s*\([^)]*Z-Library[^)]*\)', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s{2,}', ' ', title).strip()
    if title.startswith("《") and title.endswith("》"):
        parts.append(f"# {title} 伴读版")
    elif title.startswith("《"):
        parts.append(f"# {title} 伴读版")
    else:
        parts.append(f"# 《{title}》伴读版")
    # Argument-type books: inject the authored argument map right after the title.
    book_type = (data.get("book", {}).get("book_type") or "").strip().lower()
    amap_path = project / "review" / "argument_map.md"
    if book_type == "argument":
        if amap_path.is_file():
            amap = amap_path.read_text(encoding="utf-8").strip()
            if amap:
                parts.append(amap)
        else:
            print(f"WARNING: book_type=argument but {amap_path} missing; "
                  f"argument map omitted from merged file", file=sys.stderr)
    for unit in units:
        reader, _ = unit_paths(project, unit)
        text = reader.read_text(encoding="utf-8").strip()
        # Strip internal unit-id prefix (e.g. "U01 ", "U02 ") from the first heading
        text = re.sub(r'^(#+\s*)U\d+\s+', r'\1', text, count=1, flags=re.MULTILINE)
        parts.append(text)
    # Output filename: 「书名」伴读.md instead of hardcoded book-companion.md
    clean_title = title.strip("《》")
    final_name = f"{clean_title}伴读.md"
    target = project / "final" / final_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n\n---\n\n".join(parts) + "\n", encoding="utf-8")
    data["status"] = "completed"
    save_manifest(project, data)
    print(f"{target}\ncharacters: {len(target.read_text(encoding='utf-8'))}")
    if warnings:
        print(f"SOFT WARNINGS ({len(warnings)}):")
        print("\n".join(f"- {w}" for w in warnings))
    return 0


def cmd_chunk(args):
    project_dir = args.project
    """Smart paragraph sampling: reduce source text to ~30% while preserving coverage."""
    import re as _re
    from pathlib import Path
    import json
    
    project_dir = Path(project_dir)
    manifest = json.loads((project_dir / 'manifest.json').read_text(encoding='utf-8'))
    source_dir = project_dir / 'source_text'
    chunks_dir = source_dir / 'chunks'
    chunks_dir.mkdir(parents=True, exist_ok=True)
    
    total_original = 0
    total_compressed = 0
    
    for unit in manifest.get('units', []):
        uid = unit['id']
        source_files = unit.get('source_range', [])
        if not source_files:
            continue
        
        full_text = ''
        src_type = unit.get('source_type', '')
        
        if src_type == 'pdf_range':
            full_txt_path = source_dir / 'full_text.txt'
            if full_txt_path.exists():
                full_text = full_txt_path.read_text(encoding='utf-8', errors='replace')
                rng = unit.get('source_range', '')
                if '-' in rng:
                    start_page, end_page = rng.split('-')
                    lines = full_text.split('\n')
                    in_range = False
                    selected = []
                    for line in lines:
                        if start_page in line:
                            in_range = True
                        if in_range:
                            selected.append(line)
                        if end_page in line and in_range:
                            break
                    full_text = '\n'.join(selected)
        elif src_type == 'epub_chapters':
            chapters_dir = source_dir / 'chapters'
            parts = []
            for sf in source_files:
                cf = chapters_dir / f'{sf}.txt'
                if cf.exists():
                    parts.append(cf.read_text(encoding='utf-8', errors='replace'))
                else:
                    # Try to find by listing directory and matching prefix
                    found = False
                    for f in chapters_dir.iterdir():
                        if f.name.startswith(sf[:20]) and f.suffix == '.txt':
                            parts.append(f.read_text(encoding='utf-8', errors='replace'))
                            found = True
                            break
                    if not found:
                        # Fallback: try matching any file starting with the same 3-digit number
                        prefix_num = sf.split('_')[0] if '_' in sf else sf[:3]
                        for f in chapters_dir.iterdir():
                            if f.name.startswith(prefix_num) and f.suffix == '.txt':
                                parts.append(f.read_text(encoding='utf-8', errors='replace'))
                                break
            full_text = '\n'.join(parts)
        
        if not full_text.strip():
            print(f'  {uid}: empty source, skipping')
            continue
        
        # Split into paragraphs
        paragraphs = _re.split(r'\n\n+', full_text)
        if len(paragraphs) < 5:
            paragraphs = _re.split(r'\n', full_text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        
        if len(paragraphs) <= 10:
            compressed = full_text
        else:
            scored = []
            for i, para in enumerate(paragraphs):
                score = 0
                cn = sum(1 for c in para if '\u4e00' <= c <= '\u9fff')
                score += min(cn / 20, 5)
                # Adaptive: extract top-50 bigrams from this unit as domain terms
                if "_domain_terms" not in dir():
                    all_text = " ".join(paragraphs)
                    bigrams = _re.findall(r"[\u4e00-\u9fff]{2}", all_text)
                    from collections import Counter
                    _domain_terms = set(b for b, c in Counter(bigrams).most_common(50) if c >= 3)
                kw_count = sum(1 for t in _domain_terms if t in para)
                score += min(kw_count / 3, 5)
                transition_markers = ['但是', '然而', '不过', '因此', '所以', '于是',
                    '关键在于', '问题在于', '这意味着', '换句话说', '事实上', '实际上',
                    '更重要的是', '值得注意的是', '尽管如此', '恰恰相反', '另一方面',
                    '反之', '相比之下', '总而言之', '归根结底', '正因为', '由此可见']
                trans_count = sum(1 for m in transition_markers if m in para)
                score += min(trans_count * 2, 6)
                if i == 0 or i == len(paragraphs) - 1:
                    score += 3
                if _re.match(r'^第[一二三四五六七八九十]', para):
                    score += 2
                scored.append((score, para))
            
            scored.sort(key=lambda x: x[0], reverse=True)
            
            orig_cn = sum(1 for c in full_text if chr(0x4e00) <= c <= chr(0x9fff))
            target_chars = min(10000, max(3000, int(orig_cn * 0.5)))
            selected = []
            selected_chars = 0
            for score, para in scored:
                cn = sum(1 for c in para if '\u4e00' <= c <= '\u9fff')
                if selected_chars + cn > target_chars * 1.2:
                    break
                selected.append((score, para))
                selected_chars += cn
            
            selected.sort(key=lambda x: paragraphs.index(x[1]))
            compressed = '\n\n'.join(p[1] for p in selected)
        
        chunk_path = chunks_dir / f'{uid}.txt'
        chunk_path.write_text(compressed, encoding='utf-8')
        
        orig_cn = sum(1 for c in full_text if '\u4e00' <= c <= '\u9fff')
        comp_cn = sum(1 for c in compressed if '\u4e00' <= c <= '\u9fff')
        total_original += orig_cn
        total_compressed += comp_cn
        reduction = (1 - comp_cn / max(orig_cn, 1)) * 100
        print(f'  {uid}: {orig_cn} -> {comp_cn} chars ({reduction:.0f}% reduction)')
    
    if total_original > 0:
        overall = (1 - total_compressed / total_original) * 100
        print(f'  Total: {total_original} -> {total_compressed} chars ({overall:.0f}% reduction)')
    print(f'  Chunks written to {chunks_dir}')


def main():
    parser = argparse.ArgumentParser(description="Low-token book companion CLI.")
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    p = sub.add_parser("init")
    p.add_argument("project")
    p.add_argument("--book")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("extract")
    p.add_argument("project")
    p.add_argument("--book", required=True, help="Path to the book file (.pdf/.epub/.mobi/.txt)")
    p.set_defaults(func=cmd_extract)
    p = sub.add_parser("inventory")
    p.add_argument("project")
    p.set_defaults(func=cmd_inventory)
    p = sub.add_parser("chunk")
    p.add_argument("project")
    p.set_defaults(func=cmd_chunk)
    for name, func in (("status", cmd_status), ("validate", cmd_validate), ("merge", cmd_merge)):
        p = sub.add_parser(name)
        p.add_argument("project")
        p.set_defaults(func=func)
    args = parser.parse_args()
    result = args.func(args)
    raise SystemExit(result or 0)




if __name__ == "__main__":
    main()


