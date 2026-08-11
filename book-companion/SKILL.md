---
name: book-companion
description: Process a complete PDF, EPUB, or MOBI book into a high-quality standalone Chinese reader guide. Use when Codex must inventory a book, extract its text, divide it into bounded units, coordinate chapter processing, and produce a faithful reader companion.
---

# Book Companion

## Core principle

**The model reads the source text directly. Chunk is never a writing source.**

Chunk exists for one purpose: merge-stage global deduplication. When all chapters are written, a script compares chunk content against readers to flag overlapping information and prompt targeted edits.

## Workflow

```
init → extract → inventory → detect book type → define units → process units in batches → validate → dedup via chunk → merge → format
```

## Book type detection (runs after inventory)

The coordinator reads the inventory summary and classifies the book into one of three types:

| Type | Characteristics | Companion format |
|------|----------------|-----------------|
| **Argument** | Causal chain across chapters (e.g. Guns Germs Steel, 置身事内) | Each chapter reader + a "论证地图" (argument map) at the front summarizing the full causal chain |
| **Narrative** | Chronological/biographical (e.g. 梁启超, 三国前夜) | Chapter-by-chapter narrative readers preserving the original timeline and transitions |
| **Fragment** | Q&A / essay collection / year-by-year (e.g. 芒格之道) | Thematic clustering. If >25万 chars, produce condensed thematic overview instead of chapter-by-chapter |

The coordinator announces the detected type and proposed format before starting. The user can override.

## Source reading (direct, not chunk-mediated)

For each chapter:
1. Read the source text file directly (8K-20K chars per chapter)
2. Write the reader from the source text, not from chunk
3. Write evidence alongside (at minimum: source position + one original quote per major claim)
4. Self-check: one random paragraph verified against source
5. Word-count gate: immediately after writing, count Chinese chars in the completed reader (e.g. `wc -m` or a CJK counter). If below the target from the AGENTS.md word-count table, expand the reader before marking the unit complete. If above the ceiling (source chars × 80%), the reader is under-condensed — compress it before moving on. validate reports ceiling breaches as soft warnings.
6. One unit at a time: read source, write reader, self-check, save manifest. Then start the next unit. Never batch-create multiple reader files in a single pass — this breaks the self-check loop.
7. Self-audit on user flag: if the user points out systematic shortcutting (e.g. 300-word readers for 15k-word chapters, all chapters written without reading source), stop producing new content and fix existing readers first. The user is the backstop for rule 6.
8. No multi-heredoc: when writing a reader file, write exactly one file per exec_command call. Never chain multiple heredocs in the same command. After writing, verify the file does not contain shell fragments.

No two-stage navigation. No chunk scoring. No "read at least 5 windows" rules. The model reads the actual chapter text.

## Batching

| Book size | Mode |
|-----------|------|
| < 10万 chars, < 8 ch | Single session |
| 10-20万 chars, 8-15 ch | 2 batches, 4-5 ch each |
| > 20万 chars, > 15 ch | 3 batches, 4-5 ch each |

Each batch processes chapters with full source reading + evidence. Between batches: save manifest state, tell user to continue in next session.

## Chunk — one purpose only

After all chapters pass validation, run chunk to generate compressed navigation files. These are used by the merge-stage dedup script to detect overlapping content between adjacent chapters and flag for targeted edits. Chunk is never read during chapter writing.

## Commands

```
init <dir> --book <path>
extract <dir> --book <path>
inventory <dir>
chunk <dir>           # Run only at merge stage for dedup
status <dir>
validate <dir>
merge <dir>
```

## Delivery

Final Markdown, manifest, reader/, evidence/. For Argument-type books, the coordinator authors the argument map and saves it to `review/argument_map.md`; merge automatically injects it right after the title. If the file is missing, merge prints a warning and omits it.

- `assets/AGENTS.md` — project rules template.
- `references/rn-renhua.md` — de-AI-ification rules.
- `scripts/auto_format.py` — post-merge markdown cleanup.
