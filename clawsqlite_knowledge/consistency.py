# -*- coding: utf-8 -*-
"""DB/filesystem consistency checks for clawsqlite knowledge."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from .storage import resolve_local_file_path


_LIVE_RE = re.compile(r"^(\d{1,})__.+\.md$")
_BACKUP_RE = re.compile(r"^(\d{1,})__.+\.md\.bak(_deleted)?_(\d{8,14})")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _rel(path: str, root: str) -> str:
    try:
        out = os.path.relpath(path, root)
        if out != ".." and not out.startswith(".." + os.sep):
            return os.path.normpath(out)
    except Exception:
        pass
    return os.path.normpath(path)


def _entry(article_id: Optional[int], path: str, root: str, *, reason: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {"path": _rel(path, root)}
    if article_id is not None:
        out["id"] = int(article_id)
    if reason:
        out["reason"] = reason
    return out


def _live_id(name: str) -> Optional[int]:
    m = _LIVE_RE.match(name)
    return int(m.group(1)) if m else None


def _backup_match(name: str) -> Optional[re.Match[str]]:
    return _BACKUP_RE.match(name)


def _is_backup_path(path: str) -> bool:
    return _backup_match(os.path.basename(path)) is not None


def _is_live_path_for_id(path: str, article_id: int) -> bool:
    name = os.path.basename(path)
    fid = _live_id(name)
    return fid == int(article_id)


def _scan_articles_dir(articles_dir: str, root: str) -> Dict[str, Any]:
    live_files: List[Dict[str, Any]] = []
    backup_files: List[Dict[str, Any]] = []
    if not os.path.isdir(articles_dir):
        return {"live_files": live_files, "backup_files": backup_files}

    for dirpath, _, filenames in os.walk(articles_dir):
        for name in filenames:
            path = os.path.join(dirpath, name)
            m_bak = _backup_match(name)
            if m_bak:
                kind = "deleted_backup" if m_bak.group(2) else "backup"
                backup_files.append(
                    {
                        "id": int(m_bak.group(1)),
                        "path": _rel(path, root),
                        "abs_path": path,
                        "kind": kind,
                        "timestamp": m_bak.group(3),
                    }
                )
                continue

            fid = _live_id(name)
            if fid is not None:
                live_files.append({"id": fid, "path": _rel(path, root), "abs_path": path})

    live_files.sort(key=lambda item: (int(item["id"]), str(item["path"])))
    backup_files.sort(key=lambda item: (int(item["id"]), str(item["path"])))
    return {"live_files": live_files, "backup_files": backup_files}


def check_consistency(conn, *, paths: Dict[str, str]) -> Dict[str, Any]:
    """Return a structured DB/articles directory consistency report."""

    root = paths["root"]
    articles_dir = paths["articles_dir"]
    rows = conn.execute(
        "SELECT id, local_file_path, deleted_at FROM articles ORDER BY id ASC"
    ).fetchall()

    db_rows: Dict[int, Dict[str, Any]] = {}
    active_ids: set[int] = set()
    soft_deleted_ids: set[int] = set()
    active_paths: Dict[str, int] = {}
    referenced_paths: set[str] = set()

    for row in rows:
        aid = int(row["id"])
        raw_path = (row["local_file_path"] or "").strip()
        resolved = resolve_local_file_path(raw_path, root) if raw_path else ""
        deleted_at = (row["deleted_at"] or "").strip()
        is_deleted = bool(deleted_at)
        db_rows[aid] = {
            "id": aid,
            "path": raw_path,
            "resolved_path": resolved,
            "deleted_at": deleted_at,
            "deleted": is_deleted,
        }
        if resolved:
            referenced_paths.add(_norm(resolved))
        if is_deleted:
            soft_deleted_ids.add(aid)
        else:
            active_ids.add(aid)
            if resolved:
                active_paths[_norm(resolved)] = aid

    scan = _scan_articles_dir(articles_dir, root)
    live_files = scan["live_files"]
    backup_files = scan["backup_files"]
    live_by_id: Dict[int, List[Dict[str, Any]]] = {}
    backup_by_id: Dict[int, List[Dict[str, Any]]] = {}
    for item in live_files:
        live_by_id.setdefault(int(item["id"]), []).append(item)
    for item in backup_files:
        backup_by_id.setdefault(int(item["id"]), []).append(item)

    db_but_no_file: List[Dict[str, Any]] = []
    for aid in sorted(active_ids):
        row_info = db_rows[aid]
        raw_path = str(row_info["path"])
        resolved = str(row_info["resolved_path"])
        if not raw_path or not resolved:
            db_but_no_file.append({"id": aid, "path": raw_path, "reason": "missing_local_file_path"})
            continue
        if not os.path.exists(resolved):
            db_but_no_file.append(_entry(aid, resolved, root, reason="missing_live_file"))
            continue
        if _is_backup_path(resolved):
            db_but_no_file.append(_entry(aid, resolved, root, reason="db_points_to_backup_file"))
            continue
        if not _is_live_path_for_id(resolved, aid):
            db_but_no_file.append(_entry(aid, resolved, root, reason="live_file_id_mismatch"))

    file_but_no_db: List[Dict[str, Any]] = []
    soft_deleted_with_live_file: List[Dict[str, Any]] = []
    for item in live_files:
        fid = int(item["id"])
        norm_path = _norm(str(item["abs_path"]))
        if norm_path in active_paths and active_paths[norm_path] == fid:
            continue
        if fid in soft_deleted_ids:
            reason = "soft_deleted_record_has_live_file"
            soft_deleted_with_live_file.append(_entry(fid, str(item["abs_path"]), root, reason=reason))
        elif fid in active_ids:
            reason = "db_points_elsewhere"
        else:
            reason = "no_db_record"
        file_but_no_db.append(_entry(fid, str(item["abs_path"]), root, reason=reason))

    backup_only_ids = sorted(
        int(aid)
        for aid in backup_by_id
        if int(aid) not in db_rows
    )
    deleted_backup_files = [
        _entry(int(item["id"]), str(item["abs_path"]), root)
        for item in backup_files
        if item["kind"] == "deleted_backup"
    ]
    backup_files_public = [
        {
            "id": int(item["id"]),
            "path": item["path"],
            "kind": item["kind"],
            "has_db_record": int(item["id"]) in db_rows,
            "active_db_record": int(item["id"]) in active_ids,
        }
        for item in backup_files
    ]

    soft_deleted_with_backup_only: List[Dict[str, Any]] = []
    soft_deleted_without_file: List[Dict[str, Any]] = []
    for aid in sorted(soft_deleted_ids):
        has_live = aid in live_by_id
        has_backup = aid in backup_by_id
        row_info = db_rows[aid]
        resolved = str(row_info["resolved_path"])
        if resolved and os.path.exists(resolved) and _is_backup_path(resolved):
            has_backup = True
        if has_live:
            continue
        if has_backup:
            soft_deleted_with_backup_only.append(
                {
                    "id": aid,
                    "path": _rel(resolved, root) if resolved else "",
                    "reason": "soft_deleted_record_has_backup_only",
                }
            )
        else:
            soft_deleted_without_file.append(
                {
                    "id": aid,
                    "path": _rel(resolved, root) if resolved else "",
                    "reason": "soft_deleted_record_has_no_file",
                }
            )

    ok = not (
        db_but_no_file
        or file_but_no_db
        or backup_only_ids
        or deleted_backup_files
        or soft_deleted_with_live_file
        or soft_deleted_with_backup_only
    )

    next_step = (
        "DB and articles/ live files are consistent."
        if ok
        else "Review this report, then run consistency --fix for safe backup cleanup or --fix --remove-orphan-live-files if DB is authoritative."
    )

    return {
        "ok": ok,
        "summary": {
            "db_count": len(rows),
            "active_db_count": len(active_ids),
            "live_file_count": len(live_files),
            "soft_deleted_count": len(soft_deleted_ids),
            "backup_file_count": len(backup_files),
            "deleted_backup_file_count": len(deleted_backup_files),
            "missing_live_file_count": len(db_but_no_file),
            "orphan_live_file_count": len(file_but_no_db),
        },
        "db_but_no_file": db_but_no_file,
        "file_but_no_db": file_but_no_db,
        "backup_files": backup_files_public,
        "deleted_backup_files": deleted_backup_files,
        "backup_only_ids": backup_only_ids,
        "soft_deleted_ids": sorted(soft_deleted_ids),
        "soft_deleted_with_live_file": soft_deleted_with_live_file,
        "soft_deleted_with_backup_only": soft_deleted_with_backup_only,
        "soft_deleted_without_file": soft_deleted_without_file,
        "next": next_step,
    }


def apply_consistency_fixes(
    report: Dict[str, Any],
    *,
    paths: Dict[str, str],
    remove_orphan_live_files: bool = False,
) -> Dict[str, Any]:
    """Apply conservative filesystem-only consistency fixes."""

    root = paths["root"]
    deleted_backup_paths: List[str] = []
    deleted_live_paths: List[str] = []
    errors: List[Dict[str, str]] = []

    def remove_rel(rel_path: str, bucket: List[str]) -> None:
        abs_path = rel_path if os.path.isabs(rel_path) else os.path.join(root, rel_path)
        try:
            os.remove(abs_path)
            bucket.append(os.path.normpath(rel_path))
        except FileNotFoundError:
            bucket.append(os.path.normpath(rel_path))
        except Exception as e:
            errors.append({"path": rel_path, "error": str(e)})

    backup_only = set(int(x) for x in report.get("backup_only_ids", []))
    for item in report.get("backup_files", []):
        aid = int(item.get("id", 0))
        kind = str(item.get("kind", ""))
        has_db_record = bool(item.get("has_db_record", False))
        if aid in backup_only or (kind == "deleted_backup" and not has_db_record):
            remove_rel(str(item.get("path") or ""), deleted_backup_paths)

    if remove_orphan_live_files:
        for item in report.get("file_but_no_db", []):
            remove_rel(str(item.get("path") or ""), deleted_live_paths)

    return {
        "deleted_backup_files": deleted_backup_paths,
        "deleted_live_files": deleted_live_paths,
        "errors": errors,
    }
