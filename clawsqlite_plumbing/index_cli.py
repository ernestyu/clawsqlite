# -*- coding: utf-8 -*-
"""Administrative FTS / vector index maintenance primitives.

These commands operate on generic FTS / vector indexes for a given base
table. They only know about:

- base table name (`--table`),
- optional FTS virtual table (`--fts-table`),
- optional vector table (`--vec-table`).

They do *not* assume KB-specific columns like `category`.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import List, Optional


def _enable_extensions(conn: sqlite3.Connection) -> None:
    """Best-effort enabling of extensions (libsimple / vec0, etc.).

    This keeps plumbing close to the knowledge app semantics without
    hard-coding any app-specific paths. For now we only handle the
    FTS simple tokenizer, following the same env/defaults as
    clawsqlite_knowledge:

    - CLAWSQLITE_TOKENIZER_EXT overrides the path;
    - otherwise default to /usr/local/lib/libsimple.so.

    Errors are swallowed; if the extension cannot be loaded, SQLite's
    builtin tokenizer behavior is left as-is.
    """
    try:
        conn.enable_load_extension(True)
    except Exception:
        return
    ext = os.environ.get("CLAWSQLITE_TOKENIZER_EXT") or "/usr/local/lib/libsimple.so"
    if not ext or ext.lower() == "none":
        return
    try:
        conn.load_extension(ext)
    except Exception:
        # Fallback: leave FTS in builtin tokenizer mode.
        return


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
    _enable_extensions(conn)
    return conn


def _fts_columns(conn: sqlite3.Connection, fts_table: str) -> list[str]:
    """Return FTS column names in declared order (excluding rowid)."""
    cols: list[str] = []
    for row in conn.execute(f"PRAGMA table_info({fts_table})"):
        name = row["name"]
        if name:
            cols.append(str(name))
    return cols


def _cmd_check(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    try:
        base = args.table
        id_col = args.id_col
        fts = args.fts_table
        vec = args.vec_table

        base_ids = {row[0] for row in conn.execute(f"SELECT {id_col} FROM {base}")}

        if fts:
            fts_ids = {row[0] for row in conn.execute(f"SELECT rowid FROM {fts}")}
            missing_in_fts = sorted(base_ids - fts_ids)
            extra_in_fts = sorted(fts_ids - base_ids)
            if not missing_in_fts and not extra_in_fts:
                print(f"[OK] FTS index {fts} matches base table {base} ({len(base_ids)} rows)")
            else:
                print(f"[WARN] FTS index {fts} mismatch vs {base}")
                if missing_in_fts:
                    print(f"  base rows missing in FTS: {missing_in_fts[:10]}" + (" ..." if len(missing_in_fts) > 10 else ""))
                if extra_in_fts:
                    print(f"  FTS rows without base: {extra_in_fts[:10]}" + (" ..." if len(extra_in_fts) > 10 else ""))

        if vec:
            vec_ids = {row[0] for row in conn.execute(f"SELECT id FROM {vec}")}
            missing_in_vec = sorted(base_ids - vec_ids)
            extra_in_vec = sorted(vec_ids - base_ids)
            if not missing_in_vec and not extra_in_vec:
                print(f"[OK] Vec index {vec} matches base table {base} ({len(base_ids)} rows)")
            else:
                print(f"[WARN] Vec index {vec} mismatch vs {base}")
                if missing_in_vec:
                    print(f"  base rows missing in vec: {missing_in_vec[:10]}" + (" ..." if len(missing_in_vec) > 10 else ""))
                if extra_in_vec:
                    print(f"  vec rows without base: {extra_in_vec[:10]}" + (" ..." if len(extra_in_vec) > 10 else ""))

        return 0
    finally:
        conn.close()


def _cmd_rebuild(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    try:
        base = args.table
        id_col = args.id_col
        fts = args.fts_table
        vec = args.vec_table

        if fts:
            cols = _fts_columns(conn, fts)
            if not cols:
                raise SystemExit(f"ERROR: could not discover columns for FTS table {fts}")
            col_list = ", ".join(cols)
            conn.execute(f"DELETE FROM {fts}")
            # Rebuild from base table columns that match FTS schema.
            # If your base table uses different column names, create a view
            # that aligns names and point --table to the view.
            conn.execute(
                f"INSERT INTO {fts}(rowid, {col_list}) "
                f"SELECT {id_col}, {col_list} FROM {base}"
            )
            print(f"[OK] Rebuilt FTS index {fts} from {base} (id_col={id_col})")

        if vec:
            conn.execute(f"DELETE FROM {vec}")
            # plumbing does not know how to re-embed; app should call its
            # own embedder after this step.
            print(f"[OK] Cleared vector index {vec}; application must refill embeddings")

        conn.commit()
        return 0
    finally:
        conn.close()


def _cmd_search(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    try:
        fts = args.fts_table
        query = args.query
        limit = int(args.limit or 20)

        if not fts:
            raise SystemExit("ERROR: --fts-table is required for index search")

        sql = f"SELECT rowid, bm25({fts}) AS score FROM {fts} WHERE {fts} MATCH ? ORDER BY score LIMIT ?"
        for row in conn.execute(sql, (query, limit)):
            obj = {"rowid": row[0], "fts_score": row[1]}
            print(json.dumps(obj, ensure_ascii=False))
        return 0
    finally:
        conn.close()


def build_parser(prog: str = "clawsqlite admin index") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Administrative FTS / vector index maintenance commands")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # check
    p_check = sub.add_parser("check", help="Check FTS/vec index consistency")
    p_check.add_argument("--db", required=True, help="SQLite DB path")
    p_check.add_argument("--table", required=True, help="Base table name")
    p_check.add_argument("--id-col", default="id", help="Primary key column in base table (default: id)")
    p_check.add_argument("--fts-table", help="FTS table name")
    p_check.add_argument("--vec-table", help="Vector table name")
    p_check.set_defaults(func=_cmd_check)

    # rebuild
    p_rebuild = sub.add_parser("rebuild", help="Rebuild FTS/vec indexes from base table")
    p_rebuild.add_argument("--db", required=True, help="SQLite DB path")
    p_rebuild.add_argument("--table", required=True, help="Base table name")
    p_rebuild.add_argument("--id-col", default="id", help="Primary key column in base table (default: id)")
    p_rebuild.add_argument("--fts-table", help="FTS table name")
    p_rebuild.add_argument("--vec-table", help="Vector table name")
    p_rebuild.set_defaults(func=_cmd_rebuild)

    # search (optional plumbing primitive)
    p_search = sub.add_parser("search", help="Low-level FTS search; returns rowid+score JSON lines")
    p_search.add_argument("--db", required=True, help="SQLite DB path")
    p_search.add_argument("--table", required=True, help="Base table name")
    p_search.add_argument("--fts-table", required=True, help="FTS table name")
    p_search.add_argument("--query", required=True, help="Search query string")
    p_search.add_argument("--limit", type=int, default=20, help="Max results")
    p_search.set_defaults(func=_cmd_search)

    return parser


def main(argv: Optional[List[str]] = None, *, prog: str = "clawsqlite admin index") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    return int(args.func(args))
