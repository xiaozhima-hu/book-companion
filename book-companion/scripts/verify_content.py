#!/usr/bin/env python3
"""Content fidelity verification for book-companion reader files.

Spot-checks:
1. Source overlap: randomly sampled paragraphs must overlap with source text
2. Evidence quality: evidence files must contain required fields, not placeholders
"""

import re, os, sys, json, random
from pathlib import Path
from collections import Counter


def cn(text):
    return len(re.findall(r'[\u4e00-\u9fff]', text))


def extract_ngrams(text, n=3):
    """Extract n-gram character sequences from Chinese text."""
    chinese = re.findall(r'[\u4e00-\u9fff]', text)
    return [''.join(chinese[i:i+n]) for i in range(len(chinese)-n+1)]


def paragraph_overlap(reader_para, source_text, threshold=0.20):
    """Check if a reader paragraph has sufficient n-gram overlap with source."""
    reader_ngrams = set(extract_ngrams(reader_para))
    source_ngrams = set(extract_ngrams(source_text))
    
    if not reader_ngrams:
        return False, 0.0
    
    overlap = len(reader_ngrams & source_ngrams) / len(reader_ngrams)
    return overlap >= threshold, overlap


def extract_paragraphs(reader_text):
    """Extract substantive paragraphs from reader markdown."""
    paras = []
    for line in reader_text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if cn(line) > 50:  # Only substantive paragraphs
            paras.append(line)
    return paras


def check_evidence(evidence_path):
    """Check evidence file is not a placeholder."""
    if not os.path.exists(evidence_path):
        return False, "evidence file missing"
    
    with open(evidence_path) as f:
        content = f.read()
    
    if cn(content) < 50:
        return False, "evidence is placeholder/empty"
    
    # Check for required fields
    required = ['原文位置', '观点主体', '原文摘录']
    missing = [f for f in required if f not in content]
    if missing:
        return False, f"evidence missing fields: {', '.join(missing)}"
    
    return True, "evidence valid"


def verify_project(project_dir):
    """Main verification function."""
    manifest_path = os.path.join(project_dir, 'manifest.json')
    if not os.path.exists(manifest_path):
        print("ERROR: manifest.json not found")
        return False
    
    with open(manifest_path) as f:
        manifest = json.load(f)
    
    reader_dir = os.path.join(project_dir, 'reader')
    evidence_dir = os.path.join(project_dir, 'evidence')
    source_dir = os.path.join(project_dir, 'source_text')
    
    all_pass = True
    problems = []
    sample_count = 0
    fail_count = 0
    
    for unit in manifest.get('units', []):
        uid = unit['id']
        rpath = os.path.join(reader_dir, f'{uid}.md')
        epath = os.path.join(evidence_dir, f'{uid}.md')
        
        if not os.path.exists(rpath):
            continue
        
        with open(rpath) as f:
            reader_text = f.read()
        
        # ---- Evidence check ----
        ev_ok, ev_msg = check_evidence(epath)
        if not ev_ok:
            problems.append(f"{uid}: {ev_msg}")
            all_pass = False
        
        # ---- Content spot-check ----
        # Load source text
        source_text = ""
        source_type = unit.get('source_type', '')
        if source_type == 'epub_chapters':
            for ch_file in unit.get('source_range', []):
                sp = os.path.join(source_dir, 'chapters', f'{ch_file}.txt')
                if os.path.exists(sp):
                    with open(sp) as f:
                        source_text += f.read()
        elif source_type == 'pdf_range':
            ft = os.path.join(source_dir, 'full_text.txt')
            if os.path.exists(ft):
                with open(ft) as f:
                    source_text = f.read()
        
        if not source_text:
            continue
        
        # Select random paragraphs for spot-check
        paras = extract_paragraphs(reader_text)
        if not paras:
            continue
        
        sample_size = min(3, len(paras))
        samples = random.sample(paras, sample_size)
        
        for para in samples:
            ok, overlap = paragraph_overlap(para, source_text)
            sample_count += 1
            if not ok:
                fail_count += 1
                problems.append(
                    f"{uid}: content spot-check FAILED "
                    f"(overlap {overlap:.1%}, "
                    f"para starts: '{para[:60]}...')"
                )
                all_pass = False
    
    # Report
    print(f"Verified {len(manifest.get('units', []))} units")
    print(f"Evidence checks: {'PASSED' if all_pass else 'FAILED'} ({len([p for p in problems if 'evidence' in p])} issues)")
    print(f"Content spot-checks: {'PASSED' if fail_count == 0 else 'FAILED'}")
    print(f"  Sampled: {sample_count} paragraphs, failures: {fail_count}")
    
    if problems:
        print(f"\n{len(problems)} issues found:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("\nAll checks passed.")
    
    return all_pass


if __name__ == '__main__':
    if len(sys.argv) > 1:
        project_dir = sys.argv[1]
    else:
        project_dir = '.'
    
    ok = verify_project(project_dir)
    sys.exit(0 if ok else 1)
