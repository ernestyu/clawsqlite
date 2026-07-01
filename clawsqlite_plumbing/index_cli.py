# -*- coding: utf-8 -*-
"""FTS / vector index maintenance primitives for the knowledge component.

The top-level `clawsqlite admin index ...` command injects the DB path and
knowledge table defaults from clawsqlite.toml. Explicit table/path flags remain
available as recovery or debug overrides. Internally these primitives only know
about:

- base table name (`--table`),
- optional FTS virtual table (`--fts-table`),
- optional vector table (`--vec-table`).

They do *not* assume KB-specific columns like `category`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from typing import List, Optional


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str, *, label: str = "identifier") -> str:
    value = (name or "").strip()
    if not _IDENT_RE.match(value):
        raise SystemExit(f"ERROR: invalid {label}: {name!r}")
    return value


def _split_cols(value: str) -> list[str]:
    cols = [x.strip() for x in (value or "").split(",") if x.strip()]
    if not cols:
        raise SystemExit("ERROR: --fts-cols must contain at least one column name")
    return cols


def _require(value: str, label: str) -> str:
    if not value:
        raise SystemExit(
            f"ERROR: {label} is required (normally provided by clawsqlite.toml through 'clawsqlite admin')"
        )
    return value


def _enable_extensions(conn: sqlite3.Connection) -> None:
    """Best-effort enabling of tokenizer and vec extensions.

    This keeps plumbing close to the knowledge app semantics without
    hard-coding any app-specific paths. The admin index connection must be able
    to read the same virtual tables that admin embed can write.

    - CLAWSQLITE_TOKENIZER_EXT overrides the path;
    - otherwise default to /usr/local/lib/libsimple.so.
    - CLAWSQLITE_VEC_EXT points at sqlite-vec's vec0 extension when vec checks
      need to inspect an existing vec virtual table.

    Errors are swallowed; if the extension cannot be loaded, SQLite's
    builtin tokenizer behavior is left as-is, and vec checks will report the
    underlying SQLite error with a recovery hint.
    """
    try:
        conn.enable_load_extension(True)
    except Exception:
        return

    tokenizer_ext = os.environ.get("CLAWSQLITE_TOKENIZER_EXT") or "/usr/local/lib/libsimple.so"
    if tokenizer_ext and tokenizer_ext.lower() != "none":
        try:
            conn.load_extension(tokenizer_ext)
        except Exception:
            # Fallback: leave FTS in builtin tokenizer mode.
            pass

    vec_ext = os.environ.get("CLAWSQLITE_VEC_EXT")
    if vec_ext and vec_ext.lower() != "none":
        try:
            conn.load_extension(vec_ext)
        except Exception:
            # The check command will surface vec table access failures with a
            # NEXT hint; keep FTS-only checks usable.
            pass


def _open_db(path: str) -> sqlite3.Connection:
    if not path:
        print("ERROR: --db is required")
        print("NEXT: run through 'clawsqlite admin index ...' from the component root so clawsqlite.toml can provide [knowledge].db, or pass --db as an explicit recovery override")
        raise SystemExit(2)
    if not os.path.exists(path):
        print(f"ERROR: db not found at {path}")
        print("NEXT: check [knowledge].db in clawsqlite.toml, or pass --db as an explicit recovery override")
        raise SystemExit(2)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _enable_extensions(conn)
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({_ident(table, label='table')})") if row["name"]}


def _fts_columns(conn: sqlite3.Connection, fts_table: str) -> list[str]:
    """Return FTS column names in declared order (excluding rowid)."""
    cols: list[str] = []
    for row in conn.execute(f"PRAGMA table_info({_ident(fts_table, label='fts table')})"):
        name = row["name"]
        if name:
            cols.append(str(name))
    return cols


def _cmd_check(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    try:
        base = _ident(_require(args.table, "--table"), label="base table")
        id_col = _ident(args.id_col, label="id column")
        fts = _ident(args.fts_table, label="FTS table") if args.fts_table else ""
        vec = _ident(args.vec_table, label="vec table") if args.vec_table else ""

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
            try:
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
            except sqlite3.OperationalError as e:
                print(f"[WARN] Vec index {vec} could not be checked: {e}")
                print("NEXT: load sqlite-vec via CLAWSQLITE_VEC_EXT, or omit --vec-table for FTS-only checks.")

        return 0
    finally:
        conn.close()


def _cmd_rebuild(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    try:
        base = _ident(_require(args.table, "--table"), label="base table")
        id_col = _ident(args.id_col, label="id column")
        fts = _ident(args.fts_table, label="FTS table") if args.fts_table else ""
        vec = _ident(args.vec_table, label="vec table") if args.vec_table else ""

        if fts:
            cols = _split_cols(args.fts_cols) if args.fts_cols else _fts_columns(conn, fts)
            if not cols:
                raise SystemExit(f"ERROR: could not discover columns for FTS table {fts}")
            for col in cols:
                _ident(col, label="FTS column")
            fts_table_cols = set(_fts_columns(conn, fts))
            bad_fts_cols = [c for c in cols if c not in fts_table_cols]
            if bad_fts_cols:
                raise SystemExit(
                    "ERROR: --fts-cols contains columns not present in "
                    f"{fts}: {', '.join(bad_fts_cols)}"
                )
            base_cols = _table_columns(conn, base)
            missing_base_cols = [c for c in cols if c not in base_cols]
            if id_col not in base_cols:
                missing_base_cols.insert(0, id_col)
            if missing_base_cols:
                sys_msg = (
                    "ERROR: cannot rebuild FTS from base table because columns are missing: "
                    + ", ".join(dict.fromkeys(missing_base_cols))
                    + "\nNEXT: pass --fts-cols with columns that exist in the base table, "
                    "create a view with the needed columns, or use 'clawsqlite knowledge reindex --rebuild --fts' for knowledge DB body text."
                )
                raise SystemExit(sys_msg)
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
            try:
                conn.execute(f"DELETE FROM {vec}")
                # plumbing does not know how to re-embed; app should call its
                # own embedder after this step.
                print(f"[OK] Cleared vector index {vec}; application must refill embeddings")
            except sqlite3.OperationalError as e:
                print(f"[WARN] Vec index {vec} could not be cleared: {e}")
                print("NEXT: load sqlite-vec via CLAWSQLITE_VEC_EXT, or omit --vec-table for FTS-only rebuilds.")

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
            raise SystemExit("ERROR: --fts-table is required (normally provided by clawsqlite.toml through 'clawsqlite admin')")

        sql = f"SELECT rowid, bm25({fts}) AS score FROM {fts} WHERE {fts} MATCH ? ORDER BY score LIMIT ?"
        for row in conn.execute(sql, (query, limit)):
            obj = {"rowid": row[0], "fts_score": row[1]}
            print(json.dumps(obj, ensure_ascii=False))
        return 0
    finally:
        conn.close()


def build_parser(prog: str = "clawsqlite admin index") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="FTS / vector index maintenance commands for the current configured knowledge component",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # check
    p_check = sub.add_parser("check", help="Check FTS/vec index consistency")
    p_check.add_argument("--db", help="SQLite DB path override (default: [knowledge].db from clawsqlite.toml)")
    p_check.add_argument("--table", help="Base table override (default: articles)")
    p_check.add_argument("--id-col", default="id", help="Primary key column in base table (default: id)")
    p_check.add_argument("--fts-table", help="FTS table name")
    p_check.add_argument("--vec-table", help="Vector table name")
    p_check.set_defaults(func=_cmd_check)

    # rebuild
    p_rebuild = sub.add_parser("rebuild", help="Rebuild FTS/vec indexes from base table")
    p_rebuild.add_argument("--db", help="SQLite DB path override (default: [knowledge].db from clawsqlite.toml)")
    p_rebuild.add_argument("--table", help="Base table override (default: articles)")
    p_rebuild.add_argument("--id-col", default="id", help="Primary key column in base table (default: id)")
    p_rebuild.add_argument("--fts-table", help="FTS table name")
    p_rebuild.add_argument("--fts-cols", help="Comma-separated base-table columns to copy into the FTS table")
    p_rebuild.add_argument("--vec-table", help="Vector table name")
    p_rebuild.set_defaults(func=_cmd_rebuild)

    # search (optional plumbing primitive)
    p_search = sub.add_parser("search", help="Low-level FTS search; returns rowid+score JSON lines")
    p_search.add_argument("--db", help="SQLite DB path override (default: [knowledge].db from clawsqlite.toml)")
    p_search.add_argument("--table", help="Base table override (default: articles)")
    p_search.add_argument("--fts-table", help="FTS table override (default: articles_fts)")
    p_search.add_argument("--query", required=True, help="Search query string")
    p_search.add_argument("--limit", type=int, default=20, help="Max results")
    p_search.set_defaults(func=_cmd_search)

    return parser


def main(argv: Optional[List[str]] = None, *, prog: str = "clawsqlite admin index") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    return int(args.func(args))
