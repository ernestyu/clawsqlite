# -*- coding: utf-8 -*-
"""`clawsqlite index` plumbing commands.

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


def _open_db(path: str) -> sqlite3.Connection:
    if not path:
        raise SystemExit("ERROR: --db is required")
    if not os.path.exists(path):
        raise SystemExit(f"ERROR: db not found at {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _cmd_check(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    try:
        base = args.table
        fts = args.fts_table
        vec = args.vec_table

        base_ids = {row[0] for row in conn.execute(f"SELECT id FROM {base}")}

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
        fts = args.fts_table
        vec = args.vec_table

        if fts:
            conn.execute(f"DELETE FROM {fts}")
            # naive content: concat columns as text; apps can define views if needed
            conn.execute(f"INSERT INTO {fts}(rowid, title, tags, summary) SELECT id, title, tags, summary FROM {base}")
            print(f"[OK] Rebuilt FTS index {fts} from {base}")

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
        base = args.table
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


def build_parser(prog: str = "clawsqlite index") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="clawsqlite index plumbing commands")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # check
    p_check = sub.add_parser("check", help="Check FTS/vec index consistency")
    p_check.add_argument("--db", required=True, help="SQLite DB path")
    p_check.add_argument("--table", required=True, help="Base table name")
    p_check.add_argument("--fts-table", help="FTS table name")
    p_check.add_argument("--vec-table", help="Vector table name")
    p_check.set_defaults(func=_cmd_check)

    # rebuild
    p_rebuild = sub.add_parser("rebuild", help="Rebuild FTS/vec indexes from base table")
    p_rebuild.add_argument("--db", required=True, help="SQLite DB path")
    p_rebuild.add_argument("--table", required=True, help="Base table name")
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


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
