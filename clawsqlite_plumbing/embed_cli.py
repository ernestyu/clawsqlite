# -*- coding: utf-8 -*-
"""Embedding maintenance primitives for the configured knowledge component.

The top-level `clawsqlite admin embed ...` command injects DB/table/vector
defaults and embedding runtime settings from clawsqlite.toml. Explicit flags
remain available as recovery or debug overrides. Internally these commands
operate on a simple pattern:

- base table with an integer id column (e.g. `id`)
- a text column (e.g. `summary`) to be embedded
- a vec table (e.g. `articles_vec`) with schema:
      CREATE VIRTUAL TABLE articles_vec USING vec0(
          id INTEGER PRIMARY KEY,
          embedding float[DIM]
      );

The actual embedding API (OpenAI-compatible) is delegated to
`clawsqlite_knowledge.embed`; `clawsqlite admin` applies the config first.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import List, Optional


def _open_db(path: str) -> sqlite3.Connection:
    if not path:
        print("ERROR: --db is required")
        print("NEXT: run through 'clawsqlite admin embed ...' from the component root so clawsqlite.toml can provide [knowledge].db, or pass --db as an explicit recovery override")
        raise SystemExit(2)
    if not os.path.exists(path):
        print(f"ERROR: db not found at {path}")
        print("NEXT: check [knowledge].db in clawsqlite.toml, or pass --db as an explicit recovery override")
        raise SystemExit(2)
    conn = sqlite3.connect(path)
    try:
        conn.enable_load_extension(True)
        vec_ext = os.environ.get("CLAWSQLITE_VEC_EXT")
        if vec_ext:
            conn.load_extension(vec_ext)
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    return conn


def _require(value: str, label: str) -> str:
    if not value:
        raise SystemExit(
            f"ERROR: {label} is required (normally provided by clawsqlite.toml through 'clawsqlite admin')"
        )
    return value


def _cmd_embed_column(args: argparse.Namespace) -> int:
    # Deferred import to avoid hard dependency when knowledge app is absent.
    try:
        from clawsqlite_knowledge.embed import get_embedding, floats_to_f32_blob, l2_normalize
    except Exception as e:  # pragma: no cover
        raise SystemExit(f"ERROR: embedding client not available (clawsqlite_knowledge.embed): {e}")

    conn = _open_db(args.db)
    try:
        table = _require(args.table, "--table")
        id_col = _require(args.id_col, "--id-col")
        text_col = _require(args.text_col, "--text-col")
        vec_table = _require(args.vec_table, "--vec-table")
        where_clause = args.where or ""
        limit = args.limit
        offset = args.offset

        sql = f"SELECT {id_col} AS id, {text_col} AS text FROM {table}"
        params: List[object] = []
        if where_clause:
            sql += f" WHERE {where_clause}"
        sql += " ORDER BY id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        if offset is not None:
            sql += " OFFSET ?"
            params.append(int(offset))

        rows = list(conn.execute(sql, params))
        if not rows:
            print("[INFO] embed-column: no rows matched criteria")
            return 0

        conn.execute("BEGIN")
        for r in rows:
            rid = int(r["id"])
            text = (r["text"] or "").strip()
            if not text:
                continue
            try:
                emb = l2_normalize(get_embedding(text))
            except Exception as e:
                conn.rollback()
                base_url = os.environ.get("EMBEDDING_BASE_URL") or "(unset)"
                model = os.environ.get("EMBEDDING_MODEL") or "(unset)"
                sys.stderr.write(f"ERROR: embedding request failed for row id={rid}: {e}\n")
                sys.stderr.write(f"DETAIL: provider={base_url} model={model}\n")
                sys.stderr.write("NEXT: check embedding service health, API key/model config, reverse proxy, or retry later.\n")
                return 4
            blob = floats_to_f32_blob(emb)
            # Use manual DELETE + INSERT because UPSERT is not supported on vec0 virtual tables.
            conn.execute(f"DELETE FROM {vec_table} WHERE id=?", (rid,))
            conn.execute(
                f"INSERT INTO {vec_table}(id, embedding) VALUES(?, ?)",
                (rid, blob),
            )
        conn.commit()
        print(f"[OK] embed-column: processed {len(rows)} rows from {table}.{text_col} into {vec_table}")
        return 0
    finally:
        conn.close()


def build_parser(prog: str = "clawsqlite admin embed") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Embedding maintenance commands for the current configured knowledge component",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_col = sub.add_parser(
        "column",
        help=("Embed a text column into a vec table using the configured embedding service. "
              "This is a low-level primitive; applications should wrap it."),
    )
    p_col.add_argument("--db", help="SQLite DB path override (default: [knowledge].db from clawsqlite.toml)")
    p_col.add_argument("--table", help="Base table override (default: articles)")
    p_col.add_argument("--id-col", help="Primary key column override (default: id)")
    p_col.add_argument("--text-col", help="Text column override (default: summary)")
    p_col.add_argument("--vec-table", help="Vector table override (default: articles_vec)")
    p_col.add_argument("--where", help="Optional SQL WHERE clause (without 'WHERE')")
    p_col.add_argument("--limit", type=int, help="Optional LIMIT for batching")
    p_col.add_argument("--offset", type=int, help="Optional OFFSET for batching")
    p_col.set_defaults(func=_cmd_embed_column)

    return parser


def main(argv: Optional[List[str]] = None, *, prog: str = "clawsqlite admin embed") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    return int(args.func(args))
