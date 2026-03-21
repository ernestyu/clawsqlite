# -*- coding: utf-8 -*-
"""Alias entrypoint for the knowledge namespace.

This module re-exports the existing clawkb CLI under a more explicit
"knowledge" namespace. The long-term plan is:

- `clawsqlite knowledge ...` becomes the primary, documented interface for
  the knowledge base application;
- the older `clawkb` entrypoint stays as an internal / legacy name (or may
  be removed after a deprecation period).

For now we simply reuse the existing parser and commands.
"""
from __future__ import annotations

from typing import List, Optional

from .cli import build_parser as _build_kb_parser


def build_parser():
    """Build the knowledge CLI parser (currently identical to clawkb)."""

    p = _build_kb_parser()
    p.prog = "clawsqlite knowledge"
    p.description = "clawsqlite knowledge base CLI (alias of clawkb)."
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    raise SystemExit(main(_sys.argv[1:]))
