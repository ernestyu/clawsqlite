# -*- coding: utf-8 -*-
"""Administrative SQLite DB maintenance primitives.

All commands here are schema-agnostic: they operate on an arbitrary
SQLite database file and do not assume KB-specific tables.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from typing import Optional


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


def _cmd_schema(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    try:
        if args.table:
            cur = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (args.table,))
            row = cur.fetchone()
            if not row or not row["sql"]:
                sys.stdout.write(f"-- no schema found for table {args.table}\n")
            else:
                sys.stdout.write(row["sql"] + "\n")
        else:
            cur = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name")
            for row in cur.fetchall():
                sys.stdout.write(f"-- {row['type']} {row['name']} (table={row['tbl_name']})\n")
                sys.stdout.write(row["sql"] + "\n\n")
        return 0
    finally:
        conn.close()


def _cmd_exec(args: argparse.Namespace) -> int:
    if bool(args.sql) == bool(args.file):
        print("ERROR: exactly one of --sql or --file is required")
        print("NEXT: pass either --sql 'SQL...' for inline text or --file path/to/script.sql, but not both")
        raise SystemExit(2)

    conn = _open_db(args.db)
    try:
        if args.sql:
            sql_text = args.sql
        else:
            with open(args.file, "r", encoding="utf-8") as f:
                sql_text = f.read()

        sql_stripped = sql_text.strip()
        first_word = sql_stripped.split(None, 1)[0].lower() if sql_stripped else ""
        if args.sql and first_word in {"select", "with", "pragma"}:
            rows = list(conn.execute(sql_text))
            if args.json:
                print(json.dumps([dict(row) for row in rows], ensure_ascii=False))
            elif rows:
                headers = rows[0].keys()
                sys.stdout.write("\t".join(headers) + "\n")
                for row in rows:
                    sys.stdout.write("\t".join("" if row[h] is None else str(row[h]) for h in headers) + "\n")
            return 0
        else:
            conn.executescript(sql_text)
            conn.commit()
        return 0
    finally:
        conn.close()


def _cmd_vacuum(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    try:
        conn.execute("VACUUM;")
        return 0
    finally:
        conn.close()


def _cmd_analyze(args: argparse.Namespace) -> int:
    conn = _open_db(args.db)
    try:
        conn.execute("ANALYZE;")
        return 0
    finally:
        conn.close()


def _cmd_backup(args: argparse.Namespace) -> int:
    src = args.db
    if not src:
        raise SystemExit("ERROR: --db is required")
    if not os.path.exists(src):
        raise SystemExit(f"ERROR: db not found at {src}")

    out = args.out
    if not out:
        raise SystemExit("ERROR: --out is required")

    if os.path.isdir(out):
        base = os.path.splitext(os.path.basename(src))[0]
        if args.add_timestamp:
            ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
            dst = os.path.join(out, f"{base}-{ts}.db")
        else:
            dst = os.path.join(out, f"{base}.db")
    else:
        dst = out

    dst_dir = os.path.dirname(dst)
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)
    shutil.copy2(src, dst)
    sys.stdout.write(f"Backup written to {dst}\n")
    return 0


def build_parser(prog: str = "clawsqlite admin db") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Administrative SQLite database maintenance commands")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # schema
    p_schema = sub.add_parser("schema", help="Print DB schema")
    p_schema.add_argument("--db", required=True, help="SQLite DB path")
    p_schema.add_argument("--table", help="Optional table name to filter")
    p_schema.set_defaults(func=_cmd_schema)

    # exec
    p_exec = sub.add_parser("exec", help="Execute SQL text or file; inline SELECT/PRAGMA prints results")
    p_exec.add_argument("--db", required=True, help="SQLite DB path")
    p_exec.add_argument("--sql", help="Inline SQL text")
    p_exec.add_argument("--file", help="Path to .sql file")
    p_exec.add_argument("--json", action="store_true", help="Print inline SELECT/PRAGMA results as a JSON array")
    p_exec.set_defaults(func=_cmd_exec)

    # vacuum
    p_vac = sub.add_parser("vacuum", help="Run VACUUM on DB")
    p_vac.add_argument("--db", required=True, help="SQLite DB path")
    p_vac.set_defaults(func=_cmd_vacuum)

    # analyze (optional but cheap)
    p_an = sub.add_parser("analyze", help="Run ANALYZE on DB")
    p_an.add_argument("--db", required=True, help="SQLite DB path")
    p_an.set_defaults(func=_cmd_analyze)

    # backup
    p_bk = sub.add_parser("backup", help="Backup DB to a file or directory")
    p_bk.add_argument("--db", required=True, help="Source SQLite DB path")
    p_bk.add_argument("--out", required=True, help="Destination path or directory")
    p_bk.add_argument("--add-timestamp", action="store_true", help="Append UTC timestamp when --out is a directory")
    p_bk.set_defaults(func=_cmd_backup)

    return parser


def main(argv: Optional[list[str]] = None, *, prog: str = "clawsqlite admin db") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    return int(args.func(args))
