# -*- coding: utf-8 -*-
"""Top-level clawsqlite CLI.

Namespaces:

- `clawsqlite knowledge ...` – knowledge base application (Markdown + SQLite)
- `clawsqlite db ...`        – generic SQLite operations (planned)
- `clawsqlite index ...`     – FTS / vector index operations (planned)
- `clawsqlite fs ...`        – filesystem + DB helpers (planned)

设计原则：
- 顶层只解析 namespace（knowledge / db / index / fs），
  具体子命令和参数由各自子 CLI 负责解析。
- 这样可以避免在 argparse 里复制子 parser 的所有 actions，
  也不会触发 -h/--help 之类的冲突。
"""
from __future__ import annotations

import argparse
from typing import List, Optional, Tuple


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level `clawsqlite` parser.

    这里只关心第一层 namespace，后续参数全部透传给对应子 CLI。
    """
    parser = argparse.ArgumentParser(prog="clawsqlite", description="ClawSQLite CLI")
    sub = parser.add_subparsers(dest="ns", required=True)

    # knowledge namespace（知识库应用）
    sub.add_parser("knowledge", help="Knowledge base application")

    # 预留：未来可以在这里挂 db/index/fs 等子命名空间
    # sub.add_parser("db", help="Low-level SQLite operations")
    # sub.add_parser("index", help="Index (FTS / vec) operations")
    # sub.add_parser("fs", help="Filesystem + DB helpers")

    return parser


def _parse_top_level(argv: Optional[List[str]]) -> Tuple[argparse.Namespace, List[str]]:
    parser = build_parser()
    # 使用 parse_known_args，这样 knowledge 子命令的参数不会在这里被校验，
    # 而是全部透传给 clawsqlite_knowledge.knowledge_cli 自己解析。
    args, remainder = parser.parse_known_args(argv)
    return args, remainder


def main(argv: Optional[List[str]] = None) -> int:
    args, remainder = _parse_top_level(argv)

    if args.ns == "knowledge":
        # 把 namespace 之后的参数交给 knowledge CLI
        from clawsqlite_knowledge.knowledge_cli import main as knowledge_main

        return int(knowledge_main(remainder))

    # 预留：未来可以在这里处理 db/index/fs 等其它命名空间
    raise SystemExit(f"Unknown namespace: {args.ns!r}")


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    raise SystemExit(main(_sys.argv[1:]))
