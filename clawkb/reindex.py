# -*- coding: utf-8 -*-
"""
Reindex and maintenance for clawkb.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import db as dbmod
from .embed import embedding_enabled, get_embedding, floats_to_f32_blob, _embedding_missing_keys
from .generator import generate_fields
from .utils import truncate_text, comma_join_tags

def check(conn, *, embed_on: bool) -> Dict[str, Any]:
    missing = dbmod.count_missing(conn)
    file_missing = dbmod.count_file_missing(conn)
    fts_missing = dbmod.count_fts_missing(conn)
    vec_missing = dbmod.count_vec_missing(conn) if embed_on else 0
    return {
        "missing": missing,
        "file_missing": file_missing,
        "fts_missing": fts_missing,
        "vec_missing": vec_missing,
        "embedding_enabled": embed_on,
    }

def fix_missing(
    conn,
    *,
    gen_provider: str,
    embed_on: bool,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    Fill missing fields and indexes for undeleted articles.
    """
    updated = 0
    updated_vec = 0
    updated_fts = 0
    errors: List[str] = []

    rows = conn.execute("SELECT * FROM articles WHERE deleted_at IS NULL ORDER BY id ASC").fetchall()
    for r in rows:
        aid = int(r["id"])
        title = (r["title"] or "").strip()
        summary = (r["summary"] or "").strip()
        tags = (r["tags"] or "").strip()

        need_title = not title
        need_summary = not summary
        need_tags = not tags

        if need_title or need_summary or need_tags:
            # Source content for regeneration: use summary if present else fallback to reading markdown
            content = ""
            p = (r["local_file_path"] or "").strip()
            if p and os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    content = ""
            if not content:
                # last resort: use existing summary/title
                content = (summary or title or "")

            try:
                gen = generate_fields(content, hint_title=title or None, provider=gen_provider)
                new_title = title or (gen.get("title") or "").strip()
                new_summary = summary or truncate_text((gen.get("summary") or "").strip(), max_chars=1200)
                new_tags = tags or comma_join_tags(gen.get("tags"))
                dbmod.update_article_fields(conn, aid, title=new_title, summary=new_summary, tags=new_tags)
                title, summary, tags = new_title, new_summary, new_tags
                updated += 1
            except Exception as e:
                errors.append(f"id={aid}: gen failed: {e}")

        # Ensure FTS row exists
        try:
            dbmod.upsert_fts(conn, aid, title, tags, summary)
            updated_fts += 1
        except Exception as e:
            errors.append(f"id={aid}: fts upsert failed: {e}")

        # Ensure vec row exists if embedding enabled and summary exists
        if embed_on and summary:
            try:
                emb = get_embedding(summary)
                blob = floats_to_f32_blob(emb)
                dbmod.upsert_vec(conn, aid, blob)
                updated_vec += 1
            except Exception as e:
                errors.append(f"id={aid}: vec upsert failed: {e}")

    conn.commit()
    return {
        "updated_rows": updated,
        "updated_fts": updated_fts,
        "updated_vec": updated_vec,
        "errors": errors,
    }

def rebuild(
    conn,
    *,
    rebuild_fts: bool,
    rebuild_vec: bool,
    embed_on: bool,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"fts_rebuilt": False, "vec_rebuilt": False, "vec_skipped": False, "errors": []}
    if rebuild_fts:
        try:
            dbmod.rebuild_fts(conn, include_deleted=False)
            out["fts_rebuilt"] = True
        except Exception as e:
            out["errors"].append(f"fts rebuild failed: {e}")

    if rebuild_vec:
        if not embed_on:
            out["vec_skipped"] = True
        else:
            try:
                # Clear vec table then repopulate
                conn.execute("DELETE FROM articles_vec")
                rows = conn.execute(
                    "SELECT id, summary FROM articles WHERE deleted_at IS NULL AND summary IS NOT NULL AND trim(summary) != ''"
                ).fetchall()
                for r in rows:
                    aid = int(r["id"])
                    summary = str(r["summary"] or "")
                    emb = get_embedding(summary)
                    blob = floats_to_f32_blob(emb)
                    dbmod.upsert_vec(conn, aid, blob)
                out["vec_rebuilt"] = True
            except Exception as e:
                out["errors"].append(f"vec rebuild failed: {e}")

    conn.commit()
    return out
