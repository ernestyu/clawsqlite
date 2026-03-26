#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Extract keywords from a markdown file using jieba.analyse.textrank.

Usage:
    python extract_tag.py filename.md
    python extract_tag.py filename.md -k 12
    python extract_tag.py filename.md --topk 20
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import jieba.analyse


def read_text(file_path: Path) -> str:
    """
    Read text from a file as UTF-8.
    """
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Fallback for files that are not strict UTF-8
        return file_path.read_text(encoding="utf-8", errors="ignore")


def clean_markdown(text: str) -> str:
    """
    Do a light markdown cleanup before keyword extraction.

    This is intentionally simple:
    - remove fenced code blocks
    - remove inline code backticks
    - strip markdown links but keep visible text
    - strip images
    - remove headings / emphasis markers
    - normalize whitespace
    """
    # Remove fenced code blocks
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)

    # Remove inline code markers but keep content
    text = re.sub(r"`([^`]*)`", r"\1", text)

    # Remove images entirely: ![alt](url)
    text = re.sub(r"!\[[^\]]*]\([^)]+\)", " ", text)

    # Convert links [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Remove common markdown markers
    text = re.sub(r"^[>#\-\*\+\s]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~]+", " ", text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def extract_tags(text: str, topk: int) -> list[str]:
    """
    Extract tags using TextRank.

    allowPOS can be tuned. Current setting prefers nouns and verb-noun forms,
    which usually works better for article keywords than allowing all POS.
    """
    tags = jieba.analyse.textrank(
        text,
        topK=topk,
        withWeight=False,
        allowPOS=("n", "nr", "ns", "nt", "nz", "vn", "eng"),
    )
    return list(tags)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract tags from a markdown file using jieba TextRank."
    )
    parser.add_argument(
        "filename",
        help="Path to the markdown file",
    )
    parser.add_argument(
        "-k",
        "--topk",
        type=int,
        default=10,
        help="Number of tags to output (default: 10)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    file_path = Path(args.filename)

    if not file_path.exists():
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        return 1

    if args.topk <= 0:
        print("Error: --topk must be a positive integer", file=sys.stderr)
        return 1

    raw_text = read_text(file_path)
    clean_text = clean_markdown(raw_text)
    tags = extract_tags(clean_text, args.topk)

    print(tags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())