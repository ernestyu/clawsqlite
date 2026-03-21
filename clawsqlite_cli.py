# -*- coding: utf-8 -*-
"""Top-level clawsqlite CLI.

Namespaces:

- `clawsqlite knowledge ...` – knowledge base application (Markdown + SQLite)
- `clawsqlite db ...`        – generic SQLite operations
- `clawsqlite index ...`     – FTS / vector index operations
- `clawsqlite fs ...`        – filesystem + DB helpers

设计原则：
- 顶层只负责选择 namespace（knowledge / db / index / fs），
  具体子命令和参数解析完全交给各自子 CLI；
- 避免在 argparse 里复制子 parser 的 actions，
  不抢占 `-h/--help` 等选项；
- `clawsqlite --help` 给出简洁的总览。
"""
from __future__ import annotations

import argparse
from typing import List, Optional


def _print_top_level_help() -> None:
    parser = argparse.ArgumentParser(prog="clawsqlite", description="ClawSQLite CLI")
    sub = parser.add_subparsers(dest="ns")
    sub.add_parser("knowledge", help="Knowledge base application")
    sub.add_parser("db", help="Low-level SQLite operations")
    sub.add_parser("index", help="Index (FTS / vec) operations")
    sub.add_parser("fs", help="Filesystem + DB helpers")
    parser.print_help()


def main(argv: Optional[List[str]] = None) -> int:
    import sys as _sys

    if argv is None:
        argv = _sys.argv[1:]

    # No args or explicit help → show top-level help
    if not argv or argv[0] in {"-h", "--help"}:
        _print_top_level_help()
        return 0

    ns = argv[0]
    remainder = argv[1:]

    if ns == "knowledge":
        from clawsqlite_knowledge.knowledge_cli import main as knowledge_main

        return int(knowledge_main(remainder))

    if ns == "db":
        from clawsqlite_plumbing import db_cli

        return int(db_cli.main(remainder))

    if ns == "index":
        from clawsqlite_plumbing import index_cli

        return int(index_cli.main(remainder))

    if ns == "fs":
        from clawsqlite_plumbing import fs_cli

        return int(fs_cli.main(remainder))

    raise SystemExit(f"Unknown namespace: {ns!r}")


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    raise SystemExit(main(_sys.argv[1:]))
