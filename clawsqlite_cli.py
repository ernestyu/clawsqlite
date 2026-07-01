# -*- coding: utf-8 -*-
"""Top-level clawsqlite CLI.

Namespaces:

- `clawsqlite knowledge ...` – knowledge base application (Markdown + SQLite)
- `clawsqlite admin ...`     – maintenance commands for the configured component

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
    sub.add_parser("admin", help="Maintenance commands for the configured knowledge component")
    parser.print_help()


def _print_admin_help() -> None:
    parser = argparse.ArgumentParser(
        prog="clawsqlite admin",
        description=(
            "Administrative maintenance commands for the current knowledge "
            "component. These commands read the same clawsqlite.toml as "
            "'clawsqlite knowledge ...' and use its root/db/articles/runtime "
            "settings by default. Path options are explicit debug overrides."
        ),
    )
    sub = parser.add_subparsers(dest="admin_ns")
    sub.add_parser("db", help="SQLite database maintenance primitives")
    sub.add_parser("index", help="FTS / vec index maintenance primitives")
    sub.add_parser("fs", help="Filesystem + DB consistency maintenance")
    sub.add_parser("embed", help="Low-level embedding primitives")
    parser.print_help()


def _has_option(argv: List[str], option: str) -> bool:
    return any(x == option or x.startswith(option + "=") for x in argv)


def _append_default(argv: List[str], option: str, value: str) -> List[str]:
    if not value or _has_option(argv, option):
        return argv
    return argv + [option, value]


def _admin_argv_with_config_defaults(ns: str, argv: List[str], cfg) -> List[str]:
    if not argv:
        return argv
    cmd = argv[0]
    out = list(argv)

    if ns == "db":
        out = _append_default(out, "--db", cfg.db)
    elif ns == "index":
        out = _append_default(out, "--db", cfg.db)
        out = _append_default(out, "--table", "articles")
        if cmd in {"check", "rebuild", "search"}:
            out = _append_default(out, "--fts-table", "articles_fts")
        if cmd == "check":
            out = _append_default(out, "--vec-table", "articles_vec")
    elif ns == "fs":
        out = _append_default(out, "--root", cfg.articles_dir)
        out = _append_default(out, "--db", cfg.db)
        out = _append_default(out, "--table", "articles")
        out = _append_default(out, "--path-col", "local_file_path")
    elif ns == "embed":
        out = _append_default(out, "--db", cfg.db)
        if cmd == "column":
            out = _append_default(out, "--table", "articles")
            out = _append_default(out, "--id-col", "id")
            out = _append_default(out, "--text-col", "summary")
            out = _append_default(out, "--vec-table", "articles_vec")
            out = _append_default(out, "--where", "deleted_at IS NULL AND summary IS NOT NULL AND trim(summary) != ''")

    return out


def _load_admin_config():
    try:
        from clawsqlite_knowledge.config import apply_config_env, load_knowledge_config

        cfg = load_knowledge_config()
        apply_config_env(cfg)
        return cfg
    except Exception as e:
        sys.stderr.write(f"ERROR: admin requires clawsqlite.toml in the current component root: {e}\n")
        sys.stderr.write("ERROR_KIND: config_required\n")
        sys.stderr.write("NEXT: run from the directory that contains clawsqlite.toml, or create one with 'clawsqlite knowledge init-config'.\n")
        return None


def _dispatch_admin(argv: List[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        _print_admin_help()
        return 0

    ns = argv[0]
    remainder = argv[1:]
    if ns not in {"db", "index", "fs", "embed"}:
        sys.stderr.write(f"ERROR: unknown admin namespace {ns!r}\n")
        sys.stderr.write("NEXT: run 'clawsqlite admin --help' to see supported administrative namespaces.\n")
        return 2

    if any(x in {"-h", "--help"} for x in remainder):
        cfg = None
    else:
        cfg = _load_admin_config()
        if cfg is None:
            return 2
        remainder = _admin_argv_with_config_defaults(ns, remainder, cfg)

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

    raise AssertionError(f"unreachable admin namespace: {ns!r}")


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
