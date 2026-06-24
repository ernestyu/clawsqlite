# -*- coding: utf-8 -*-
"""Alias entrypoint for the knowledge namespace.

This module exposes the knowledge CLI under:

  `clawsqlite knowledge ...`
"""
from __future__ import annotations

from typing import List, Optional

from .cli import build_parser as _build_kb_parser
from .cli import main as _main


def build_parser():
    """Build the knowledge CLI parser."""

    p = _build_kb_parser()
    p.prog = "clawsqlite knowledge"
    p.description = "clawsqlite knowledge base CLI."
    return p


def main(argv: Optional[List[str]] = None) -> int:
    return int(_main(argv))


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    raise SystemExit(main(_sys.argv[1:]))
