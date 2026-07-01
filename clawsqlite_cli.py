# -*- coding: utf-8 -*-
"""Top-level clawsqlite CLI.

Namespaces:

- `clawsqlite knowledge ...` – knowledge base application (Markdown + SQLite)
- `clawsqlite admin ...`     – administrative / low-level maintenance commands

设计原则：
- 顶层只负责选择 namespace（knowledge / admin），
  具体子命令和参数解析完全交给各自子 CLI；
- 避免在 argparse 里复制子 parser 的 actions，
  不抢占 `-h/--help` 等选项；
- `clawsqlite --help` 给出简洁的总览。
"""
from __future__ import annotations

import argparse
from typing import List, Optional
import sys


def _print_top_level_help() -> None:
    parser = argparse.ArgumentParser(prog="clawsqlite", description="ClawSQLite CLI")
    sub = parser.add_subparsers(dest="ns")
    sub.add_parser("knowledge", help="Knowledge base application")
    sub.add_parser("admin", help="Administrative / low-level maintenance commands")
    parser.print_help()


def _print_admin_help() -> None:
    parser = argparse.ArgumentParser(
        prog="clawsqlite admin",
        description=(
            "Administrative / low-level maintenance commands. These commands are "
            "intended for advanced users, operators, and recovery or diagnostic "
            "workflows. Normal knowledge-base usage should prefer "
            "'clawsqlite knowledge ...'."
        ),
    )
    sub = parser.add_subparsers(dest="admin_ns")
    sub.add_parser("db", help="SQLite database maintenance primitives")
    sub.add_parser("index", help="FTS / vec index maintenance primitives")
    sub.add_parser("fs", help="Filesystem + DB consistency maintenance")
    sub.add_parser("embed", help="Low-level embedding primitives")
    parser.print_help()


def _dispatch_admin(argv: List[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        _print_admin_help()
        return 0

    ns = argv[0]
    remainder = argv[1:]

    if ns == "db":
        from clawsqlite_plumbing import db_cli

        return int(db_cli.main(remainder, prog="clawsqlite admin db"))

    if ns == "index":
        from clawsqlite_plumbing import index_cli

        return int(index_cli.main(remainder, prog="clawsqlite admin index"))

    if ns == "fs":
        from clawsqlite_plumbing import fs_cli

        return int(fs_cli.main(remainder, prog="clawsqlite admin fs"))

    if ns == "embed":
        from clawsqlite_plumbing import embed_cli

        return int(embed_cli.main(remainder, prog="clawsqlite admin embed"))

    sys.stderr.write(f"ERROR: unknown admin namespace {ns!r}\n")
    sys.stderr.write("NEXT: run 'clawsqlite admin --help' to see supported administrative namespaces.\n")
    return 2


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

    if ns == "admin":
        return _dispatch_admin(remainder)

    sys.stderr.write(f"ERROR: unknown namespace {ns!r}\n")
    sys.stderr.write("NEXT: run 'clawsqlite --help' to see supported namespaces and usage.\n")
    return 2


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    raise SystemExit(main(_sys.argv[1:]))
