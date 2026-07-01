# -*- coding: utf-8 -*-
"""Administrative filesystem / DB consistency maintenance primitives.

Helpers for applications that pair a SQLite DB with a filesystem tree.
They assume:

- a root directory `--root` where content files live,
- a DB table `--table` with a column `--path-col` storing relative paths.

No KB-specific semantics are baked in.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import List, Optional


def _open_db(path: str) -> sqlite3.Connection:
    if not path:
        print("ERROR: --db is required")
        print("NEXT: pass --db /path/to/your.db (or use 'clawsqlite knowledge' if you meant the knowledge DB)")
        raise SystemExit(2)
    if not os.path.exists(path):
        print(f"ERROR: db not found at {path}")
        print("NEXT: check the path, or run 'clawsqlite knowledge ... --root <dir>' to let clawsqlite manage the DB")
        raise SystemExit(2)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _scan_fs(root: str) -> set[str]:
    files: set[str] = set()
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            files.add(rel)
    return files


def _normalize_db_path(root: str, path: str) -> str:
    p = (path or "").strip()
    if not p:
        return ""
    if os.path.isabs(p):
        try:
            return os.path.relpath(p, root)
        except Exception:
            return p
    return p


def _classify_path(path: str) -> str:
    name = os.path.basename(path)
    if ".bak_" in path or name.endswith(".bak"):
        return "backup"
    if name.endswith("-wal") or name.endswith("-shm") or name.endswith(".sqlite3-wal") or name.endswith(".sqlite3-shm"):
        return "sqlite_sidecar"
    return "regular"


def _mismatch_payload(fs_only: list[str], db_only: list[str]) -> dict:
    fs_items = [{"path": p, "kind": _classify_path(p)} for p in fs_only]
    db_items = [{"path": p, "kind": _classify_path(p)} for p in db_only]
    return {
        "summary": {
            "fs_only": len(fs_items),
            "db_only": len(db_items),
            "fs_only_by_kind": _count_kinds(fs_items),
            "db_only_by_kind": _count_kinds(db_items),
        },
        "fs_only": fs_items,
        "db_only": db_items,
    }


def _count_kinds(items: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        kind = str(item.get("kind") or "regular")
        out[kind] = out.get(kind, 0) + 1
    return out


def _print_mismatches(payload: dict, *, json_out: bool) -> None:
    if json_out:
        print(json.dumps(payload, ensure_ascii=False))
        return
    summary = payload["summary"]
    print(f"[SUMMARY] FS_ONLY={summary['fs_only']} DB_ONLY={summary['db_only']}")
    for item in payload["fs_only"]:
        print(f"[FS_ONLY][{item['kind']}] {item['path']}")
    for item in payload["db_only"]:
        print(f"[DB_ONLY][{item['kind']}] {item['path']}")


def _cmd_list_orphans(args: argparse.Namespace) -> int:
    root = args.root
    conn = _open_db(args.db)
    try:
        fs_paths = _scan_fs(root)
        db_paths = set()
        for row in conn.execute(f"SELECT {args.path_col} AS p FROM {args.table}"):
            p = _normalize_db_path(root, row["p"] or "")
            if p:
                db_paths.add(p)

        fs_only = sorted(fs_paths - db_paths)
        db_only = sorted(db_paths - fs_paths)
        _print_mismatches(_mismatch_payload(fs_only, db_only), json_out=bool(args.json))
        return 0
    finally:
        conn.close()


def _cmd_gc(args: argparse.Namespace) -> int:
    root = args.root
    conn = _open_db(args.db)
    try:
        fs_paths = _scan_fs(root)
        db_rows = []
        for row in conn.execute(f"SELECT rowid AS _rowid, {args.path_col} AS p FROM {args.table}"):
            db_rows.append((row["_rowid"], _normalize_db_path(root, row["p"] or "")))

        db_paths = {p for _, p in db_rows if p}
        fs_only = sorted(fs_paths - db_paths)
        db_only = sorted(db_paths - fs_paths)

        # FS orphans
        if args.delete_fs_orphans:
            for rel in fs_only:
                full = os.path.join(root, rel)
                if args.dry_run:
                    print(f"[DRY_RUN][DELETE_FS] {rel}")
                else:
                    try:
                        os.remove(full)
                        print(f"[DELETE_FS] {rel}")
                    except Exception as e:
                        print(f"[ERROR][DELETE_FS] {rel}: {e}")
        else:
            for rel in fs_only:
                print(f"[FS_ONLY] {rel}")

        # DB orphans
        if args.delete_db_orphans:
            for rowid, rel in db_rows:
                if rel and rel in db_only:
                    if args.dry_run:
                        print(f"[DRY_RUN][DELETE_DB] rowid={rowid} path={rel}")
                    else:
                        conn.execute(f"DELETE FROM {args.table} WHERE rowid=?", (rowid,))
                        print(f"[DELETE_DB] rowid={rowid} path={rel}")
            if not args.dry_run:
                conn.commit()
        else:
            for rel in db_only:
                print(f"[DB_ONLY] {rel}")

        return 0
    finally:
        conn.close()


def build_parser(prog: str = "clawsqlite admin fs") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Administrative filesystem + DB consistency maintenance commands")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # list-orphans
    p_list = sub.add_parser("list-orphans", help="List FS/DB mismatches")
    p_list.add_argument("--root", required=True, help="Content root directory")
    p_list.add_argument("--db", required=True, help="SQLite DB path")
    p_list.add_argument("--table", required=True, help="Table that stores file paths")
    p_list.add_argument("--path-col", required=True, help="Column name that stores relative paths")
    p_list.add_argument("--json", action="store_true", help="Print mismatch summary and paths as JSON")
    p_list.set_defaults(func=_cmd_list_orphans)

    # gc
    p_gc = sub.add_parser("gc", help="Garbage-collect FS/DB orphans")
    p_gc.add_argument("--root", required=True, help="Content root directory")
    p_gc.add_argument("--db", required=True, help="SQLite DB path")
    p_gc.add_argument("--table", required=True, help="Table that stores file paths")
    p_gc.add_argument("--path-col", required=True, help="Column name that stores relative paths")
    p_gc.add_argument("--delete-fs-orphans", action="store_true", help="Delete files not referenced by DB")
    p_gc.add_argument("--delete-db-orphans", action="store_true", help="Delete DB rows whose files are missing")
    p_gc.add_argument("--dry-run", action="store_true", help="Only print actions, do not modify FS/DB")
    p_gc.set_defaults(func=_cmd_gc)

    return parser


def main(argv: Optional[List[str]] = None, *, prog: str = "clawsqlite admin fs") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    return int(args.func(args))
