# -*- coding: utf-8 -*-
"""Top-level clawsqlite CLI.

Namespaces:

- `clawsqlite knowledge ...` – knowledge base application (Markdown + SQLite)
- `clawsqlite db ...`        – generic SQLite operations (planned)
- `clawsqlite index ...`     – FTS / vector index operations (planned)
- `clawsqlite fs ...`        – filesystem + DB helpers (planned)
"""
from __future__ import annotations

import argparse
from typing import List, Optional

from clawsqlite_knowledge.knowledge_cli import build_parser as build_knowledge_parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clawsqlite", description="ClawSQLite CLI")
    sub = parser.add_subparsers(dest="ns", required=True)

    # knowledge namespace
    p_knowledge = sub.add_parser("knowledge", help="Knowledge base application")
    # Reuse existing knowledge parser structure
    knowledge_parser = build_knowledge_parser()
    # Copy arguments from knowledge_parser into this subparser
    for action in knowledge_parser._actions:  # type: ignore[attr-defined]
        if action.option_strings or action.nargs != 0:
            # Skip the subparsers action itself; we just want the options/args
            if isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
                p_knowledge._subparsers = action  # type: ignore[attr-defined]
            else:
                p_knowledge._add_action(action)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.ns == "knowledge":
        # Delegate to knowledge CLI
        from clawsqlite_knowledge.knowledge_cli import main as knowledge_main

        return int(knowledge_main(argv[1:])) if argv is not None else int(knowledge_main())

    parser.error("Unknown namespace")
    return 2


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    raise SystemExit(main(_sys.argv[1:]))
