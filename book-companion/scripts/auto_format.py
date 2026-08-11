#!/usr/bin/env python3
"""Post-merge markdown cleanup for the final companion file.

Fixes:
  1. Ensure a blank line after every heading line.
  2. Collapse runs of 2+ blank lines into one.
  3. Strip trailing whitespace on every line.
  4. Ensure the file ends with exactly one newline.

Usage: auto_format.py <path-to-md>
"""
import re
import sys
from pathlib import Path


def clean(text: str) -> str:
    # Strip trailing whitespace on every line.
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    # Insert a blank line after headings that are immediately followed by text.
    text = re.sub(r"^(#{1,6} .+)\n(?!\n)(?!#)", r"\1\n\n", text, flags=re.MULTILINE)
    # Collapse 2+ blank lines into one.
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Exactly one trailing newline.
    return text.rstrip("\n") + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: auto_format.py <path-to-md>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1
    original = path.read_text(encoding="utf-8")
    cleaned = clean(original)
    path.write_text(cleaned, encoding="utf-8")
    changed = "unchanged" if cleaned == original else "reformatted"
    print(f"{path}: {changed} ({len(original)} -> {len(cleaned)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
