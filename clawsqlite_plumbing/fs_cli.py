# -*- coding: utf-8 -*-
"""Filesystem / DB consistency maintenance for the knowledge component.

The top-level `clawsqlite admin fs ...` command injects root/db/table/path
defaults from clawsqlite.toml. Explicit flags remain available as recovery or
debug overrides. Internally these helpers assume:

- a root directory `--root` where content files live,
- a DB table `--table` with a column `--path-col` storing relative paths.

The repair command is knowledge-component aware: it reconstructs missing
article Markdown files from configured scraper output or DB summaries.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sqlite3
import sys
from typing import Any, List, Optional


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _open_db(path: str) -> sqlite3.Connection:
    if not path:
        print("ERROR: --db is required")
        print("NEXT: run through 'clawsqlite admin fs ...' from the component root so clawsqlite.toml can provide [knowledge].db, or pass --db as an explicit recovery override")
        raise SystemExit(2)
    if not os.path.exists(path):
        print(f"ERROR: db not found at {path}")
        print("NEXT: check [knowledge].db in clawsqlite.toml, or pass --db as an explicit recovery override")
        raise SystemExit(2)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _require(value: str, label: str) -> str:
    if not value:
        raise SystemExit(
            f"ERROR: {label} is required (normally provided by clawsqlite.toml through 'clawsqlite admin')"
        )
    return value


def _ident(name: str, *, label: str = "identifier") -> str:
    value = (name or "").strip()
    if not _IDENT_RE.match(value):
        raise SystemExit(f"ERROR: invalid {label}: {name!r}")
    return value


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


def _full_path_under_root(root: str, path: str) -> Optional[str]:
    p = (path or "").strip()
    if not p:
        return None
    full = p if os.path.isabs(p) else os.path.join(root, p)
    full = os.path.abspath(full)
    root_abs = os.path.abspath(root)
    try:
        if os.path.commonpath([root_abs, full]) != root_abs:
            return None
    except Exception:
        return None
    return full


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
        "items": (
            [{"kind": "fs_only", "class": item["kind"], "path": item["path"]} for item in fs_items]
            + [{"kind": "db_only", "class": item["kind"], "path": item["path"]} for item in db_items]
        ),
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
    root = _require(args.root, "--root")
    table = _ident(_require(args.table, "--table"), label="table")
    path_col = _ident(_require(args.path_col, "--path-col"), label="path column")
    conn = _open_db(args.db)
    try:
        fs_paths = _scan_fs(root)
        db_paths = set()
        for row in conn.execute(f"SELECT {path_col} AS p FROM {table}"):
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
    root = _require(args.root, "--root")
    table = _ident(_require(args.table, "--table"), label="table")
    path_col = _ident(_require(args.path_col, "--path-col"), label="path column")
    conn = _open_db(args.db)
    try:
        fs_paths = _scan_fs(root)
        db_rows = []
        for row in conn.execute(f"SELECT rowid AS _rowid, {path_col} AS p FROM {table}"):
            db_rows.append((row["_rowid"], _normalize_db_path(root, row["p"] or "")))

        db_paths = {p for _, p in db_rows if p}
        fs_only = sorted(fs_paths - db_paths)
        db_only = sorted(db_paths - fs_paths)

        result: dict[str, Any] = {
            "dry_run": bool(args.dry_run),
            "deleted_fs": [],
            "deleted_db": [],
            "skipped": [],
            "summary": {
                "fs_only": len(fs_only),
                "db_only": len(db_only),
                "deleted_fs_count": 0,
                "deleted_db_count": 0,
                "skipped_count": 0,
                "fs_only_by_kind": _count_kinds([{"kind": _classify_path(p)} for p in fs_only]),
                "db_only_by_kind": _count_kinds([{"kind": _classify_path(p)} for p in db_only]),
            },
        }

        if args.delete_fs_orphans:
            for rel in fs_only:
                full = os.path.join(root, rel)
                if args.dry_run:
                    result["deleted_fs"].append(rel)
                    if not args.json:
                        print(f"[DRY_RUN][DELETE_FS] {rel}")
                else:
                    try:
                        os.remove(full)
                        result["deleted_fs"].append(rel)
                        if not args.json:
                            print(f"[DELETE_FS] {rel}")
                    except Exception as e:
                        result["skipped"].append({"kind": "fs_only", "path": rel, "reason": str(e)})
                        if not args.json:
                            print(f"[ERROR][DELETE_FS] {rel}: {e}")
        else:
            for rel in fs_only:
                result["skipped"].append({"kind": "fs_only", "path": rel, "reason": "delete_fs_orphans_not_enabled"})
                if not args.json:
                    print(f"[FS_ONLY] {rel}")

        if args.delete_db_orphans:
            for rowid, rel in db_rows:
                if rel and rel in db_only:
                    if args.dry_run:
                        result["deleted_db"].append({"rowid": rowid, "path": rel})
                        if not args.json:
                            print(f"[DRY_RUN][DELETE_DB] rowid={rowid} path={rel}")
                    else:
                        conn.execute(f"DELETE FROM {table} WHERE rowid=?", (rowid,))
                        result["deleted_db"].append({"rowid": rowid, "path": rel})
                        if not args.json:
                            print(f"[DELETE_DB] rowid={rowid} path={rel}")
            if not args.dry_run:
                conn.commit()
        else:
            for rel in db_only:
                result["skipped"].append({"kind": "db_only", "path": rel, "reason": "delete_db_orphans_not_enabled"})
                if not args.json:
                    print(f"[DB_ONLY] {rel}")

        result["summary"]["deleted_fs_count"] = len(result["deleted_fs"])
        result["summary"]["deleted_db_count"] = len(result["deleted_db"])
        result["summary"]["skipped_count"] = len(result["skipped"])
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(
                "[SUMMARY] "
                f"deleted_fs_count={result['summary']['deleted_fs_count']} "
                f"deleted_db_count={result['summary']['deleted_db_count']} "
                f"skipped_count={result['summary']['skipped_count']}"
            )

        return 0
    finally:
        conn.close()


def _available_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})") if row["name"]}


def _row_value(row: sqlite3.Row, key: str, default: Any = "") -> Any:
    try:
        value = row[key]
    except Exception:
        return default
    return default if value is None else value


def _is_fetchable_url(value: str) -> bool:
    s = (value or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://")


def _repair_body_from_row(row: sqlite3.Row) -> str:
    summary = str(_row_value(row, "summary", "") or "").strip()
    title = str(_row_value(row, "title", "") or "").strip()
    if summary:
        return summary
    if title:
        return title
    return "Recovered placeholder body. The original article file was missing."


def _format_repair_markdown(row: sqlite3.Row, *, body: str, title: str) -> str:
    try:
        from clawsqlite_knowledge.storage import format_markdown_with_metadata

        return format_markdown_with_metadata(
            article_id=int(_row_value(row, "id", _row_value(row, "_rowid", 0)) or 0),
            title=title,
            source_url=str(_row_value(row, "source_url", "") or ""),
            created_at=str(_row_value(row, "created_at", "") or _dt.datetime.utcnow().isoformat() + "Z"),
            category=str(_row_value(row, "category", "") or ""),
            tags=str(_row_value(row, "tags", "") or ""),
            priority=int(_row_value(row, "priority", 0) or 0),
            body_markdown=body,
            summary=str(_row_value(row, "summary", "") or ""),
            generation_quality=str(_row_value(row, "generation_quality", "") or ""),
            summary_model=str(_row_value(row, "summary_model", "") or ""),
            tags_model=str(_row_value(row, "tags_model", "") or ""),
            embedding_model=str(_row_value(row, "embedding_model", "") or ""),
            content_type=str(_row_value(row, "content_type", "") or ""),
            key_claims=str(_row_value(row, "key_claims", "") or ""),
        )
    except Exception:
        return (
            "--- METADATA ---\n"
            f"id: {_row_value(row, 'id', _row_value(row, '_rowid', ''))}\n"
            f"title: {title}\n"
            f"source_url: {_row_value(row, 'source_url', '')}\n"
            "--- SUMMARY ---\n"
            f"{_row_value(row, 'summary', '')}\n"
            "--- MARKDOWN ---\n"
            f"{body.rstrip()}\n"
        )


def _cmd_repair(args: argparse.Namespace) -> int:
    root = _require(args.root, "--root")
    table = _ident(_require(args.table, "--table"), label="table")
    path_col = _ident(_require(args.path_col, "--path-col"), label="path column")
    conn = _open_db(args.db)
    try:
        cols = _available_columns(conn, table)
        wanted = [
            "_rowid",
            path_col,
            "id",
            "title",
            "source_url",
            "summary",
            "created_at",
            "category",
            "tags",
            "priority",
            "generation_quality",
            "summary_model",
            "tags_model",
            "embedding_model",
            "content_type",
            "key_claims",
        ]
        select_parts = ["rowid AS _rowid", f"{path_col} AS {path_col}"]
        for col in wanted:
            if col in {"_rowid", path_col}:
                continue
            if col in cols:
                select_parts.append(col)
        rows = list(conn.execute(f"SELECT {', '.join(select_parts)} FROM {table}"))
        repaired = []
        skipped = []
        warnings = []
        for row in rows:
            raw_path = str(row[path_col] or "").strip()
            rel = _normalize_db_path(root, raw_path)
            if not rel:
                skipped.append({"rowid": row["_rowid"], "path": raw_path, "reason": "empty_path"})
                continue
            full = _full_path_under_root(root, raw_path)
            if not full:
                skipped.append({"rowid": row["_rowid"], "path": raw_path, "reason": "path_outside_root"})
                continue
            if os.path.exists(full):
                continue

            title = str(_row_value(row, "title", "") or f"Recovered {row['_rowid']}").strip()
            source_url = str(_row_value(row, "source_url", "") or "").strip()
            body = ""
            mode = "summary"
            if _is_fetchable_url(source_url) and not args.no_scrape:
                try:
                    from clawsqlite_knowledge.scraper import scrape_url

                    scraped_title, scraped_body = scrape_url(source_url, timeout=int(args.scrape_timeout))
                    if scraped_body:
                        body = scraped_body
                        mode = "scrape"
                    if scraped_title:
                        title = scraped_title
                except Exception as e:
                    mode = "summary_fallback"
                    warnings.append({"rowid": row["_rowid"], "path": rel, "warning": f"scrape_failed: {e}"})
            if not body:
                body = _repair_body_from_row(row)

            content = _format_repair_markdown(row, body=body, title=title)
            item = {"rowid": row["_rowid"], "path": rel, "mode": mode, "class": _classify_path(rel)}
            if args.dry_run:
                item["dry_run"] = True
            else:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
            repaired.append(item)
            if args.limit is not None and len(repaired) >= int(args.limit):
                break

        result = {
            "dry_run": bool(args.dry_run),
            "repaired": repaired,
            "skipped": skipped,
            "warnings": warnings,
            "summary": {
                "repaired_count": len(repaired),
                "skipped_count": len(skipped),
                "warning_count": len(warnings),
                "dry_run": bool(args.dry_run),
            },
        }
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            for item in repaired:
                prefix = "[DRY_RUN][REPAIR]" if args.dry_run else "[REPAIR]"
                print(f"{prefix}[{item['mode']}] rowid={item['rowid']} path={item['path']}")
            for item in skipped:
                print(f"[SKIP] rowid={item.get('rowid')} path={item.get('path')} reason={item.get('reason')}", file=sys.stderr)
            for item in warnings:
                print(f"[WARN] rowid={item.get('rowid')} path={item.get('path')} {item.get('warning')}", file=sys.stderr)
            print(f"[SUMMARY] repaired_count={len(repaired)} skipped_count={len(skipped)} warning_count={len(warnings)}")
        return 0
    finally:
        conn.close()


def build_parser(prog: str = "clawsqlite admin fs") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Filesystem + DB consistency maintenance commands for the current configured knowledge component",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # list-orphans
    p_list = sub.add_parser("list-orphans", help="List FS/DB mismatches")
    p_list.add_argument("--root", help="Content root override (default: [knowledge].articles_dir from clawsqlite.toml)")
    p_list.add_argument("--db", help="SQLite DB path override (default: [knowledge].db from clawsqlite.toml)")
    p_list.add_argument("--table", help="Table override (default: articles)")
    p_list.add_argument("--path-col", help="Path column override (default: local_file_path)")
    p_list.add_argument("--json", action="store_true", help="Print mismatch summary and paths as JSON")
    p_list.set_defaults(func=_cmd_list_orphans)

    # gc
    p_gc = sub.add_parser("gc", help="Garbage-collect FS/DB orphans")
    p_gc.add_argument("--root", help="Content root override (default: [knowledge].articles_dir from clawsqlite.toml)")
    p_gc.add_argument("--db", help="SQLite DB path override (default: [knowledge].db from clawsqlite.toml)")
    p_gc.add_argument("--table", help="Table override (default: articles)")
    p_gc.add_argument("--path-col", help="Path column override (default: local_file_path)")
    p_gc.add_argument("--delete-fs-orphans", action="store_true", help="Delete files not referenced by DB")
    p_gc.add_argument("--delete-db-orphans", action="store_true", help="Delete DB rows whose files are missing")
    p_gc.add_argument("--dry-run", action="store_true", help="Only print actions, do not modify FS/DB")
    p_gc.add_argument("--json", action="store_true", help="Print structured cleanup result as JSON")
    p_gc.set_defaults(func=_cmd_gc)

    # repair
    p_repair = sub.add_parser(
        "repair",
        aliases=["reconstruct", "restore-missing"],
        help="Recreate missing article markdown files from scraper output or DB summaries",
    )
    p_repair.add_argument("--root", help="Content root override (default: [knowledge].articles_dir from clawsqlite.toml)")
    p_repair.add_argument("--db", help="SQLite DB path override (default: [knowledge].db from clawsqlite.toml)")
    p_repair.add_argument("--table", help="Table override (default: articles)")
    p_repair.add_argument("--path-col", help="Path column override (default: local_file_path)")
    p_repair.add_argument("--no-scrape", action="store_true", help="Do not re-fetch source_url records; reconstruct from DB summary/title only")
    p_repair.add_argument("--scrape-timeout", type=int, default=120, help="Seconds to wait for configured scraper per URL")
    p_repair.add_argument("--limit", type=int, help="Maximum number of missing files to repair")
    p_repair.add_argument("--dry-run", action="store_true", help="Report repair actions without writing files")
    p_repair.add_argument("--json", action="store_true", help="Print structured repair result as JSON")
    p_repair.set_defaults(func=_cmd_repair)

    return parser


def main(argv: Optional[List[str]] = None, *, prog: str = "clawsqlite admin fs") -> int:
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    return int(args.func(args))
