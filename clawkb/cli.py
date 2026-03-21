# -*- coding: utf-8 -*-
"""
clawkb CLI entrypoint.

Implements:
- ingest / reindex / search / show / update / delete / export

Note on CLI ergonomics:
- Common flags (--db/--root/--articles-dir/--json/--verbose/...) are accepted BOTH before and after the subcommand.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import datetime as _dt
from typing import Any, Dict, List, Optional

from . import db as dbmod
from .utils import now_iso_z, truncate_text, comma_join_tags, resolve_root_paths
from .storage import (
    ensure_dir,
    article_abspath,
    format_markdown_with_metadata,
    read_markdown,
    write_markdown,
)
from .generator import generate_fields
from .embed import embedding_enabled, get_embedding, floats_to_f32_blob, _embedding_missing_keys
from .scraper import scrape_url
from .search import hybrid_search
from . import reindex as reindex_mod

DEFAULT_ROOT = os.environ.get("CLAWKB_ROOT_DEFAULT", "")


def _resolve_paths(args) -> Dict[str, str]:
    """Resolve root/db/articles-dir with clear priority: CLI > env > defaults.

    DEFAULT_ROOT is intentionally kept lightweight; the actual fallback
    logic lives in utils.resolve_root_paths so it can be reused by other
    entrypoints if needed.
    """

    return resolve_root_paths(
        cli_root=getattr(args, "root", None),
        cli_db=getattr(args, "db", None),
        cli_articles_dir=getattr(args, "articles_dir", None),
        default_root=DEFAULT_ROOT or None,
    )

def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    else:
        if isinstance(obj, str):
            sys.stdout.write(obj + "\n")
        else:
            sys.stdout.write(str(obj) + "\n")

def _open_for_command(db_path: str, *, need_fts: bool, need_vec: bool, args) -> Any:
    tokenizer_ext = getattr(args, "tokenizer_ext", None)
    vec_ext = getattr(args, "vec_ext", None)
    return dbmod.open_db(db_path, need_fts=need_fts, need_vec=need_vec, tokenizer_ext=tokenizer_ext, vec_ext=vec_ext)

def cmd_ingest(args) -> int:
    paths = _resolve_paths(args)
    ensure_dir(paths["articles_dir"])

    source_url = args.url or "Local"
    category = args.category or ""
    priority = int(args.priority or 0)
    gen_provider = (args.gen_provider or "openclaw").lower()

    # 1) Fetch content
    hint_title = args.title
    body_md = ""
    if args.url:
        try:
            t, md = scrape_url(args.url, scrape_cmd=args.scrape_cmd)
            if t and not hint_title:
                hint_title = t
            body_md = md
        except Exception as e:
            sys.stderr.write(f"ERROR: scrape failed: {e}\n")
            return 3
    else:
        body_md = args.text

    # 2) Fields
    tags = args.tags or ""
    summary = args.summary or ""
    title = hint_title or ""

    if gen_provider != "off":
        need_gen = (not title) or (not summary) or (not tags and not args.tags)
        if need_gen:
            try:
                # generate_fields itself will strip obvious metadata/header
                # noise (e.g. our own --- METADATA ---/--- MARKDOWN ---
                # blocks and WeChat-style reading stats) before applying
                # heuristics or calling the small LLM.
                gen = generate_fields(
                    body_md,
                    hint_title=title or None,
                    provider=gen_provider,
                    max_summary_chars=args.max_summary_chars,
                )
                if not title:
                    title = (gen.get("title") or "").strip()
                if not summary:
                    summary = (gen.get("summary") or "").strip()
                if not tags and not args.tags:
                    tags = comma_join_tags(gen.get("tags"))
            except Exception as e:
                sys.stderr.write(f"WARNING: generate_fields failed: {e}\n")

    title = title.strip() or "untitled"
    summary = truncate_text(summary, max_chars=args.max_summary_chars)
    tags = comma_join_tags(tags)

    created_at = now_iso_z()

    conn = None
    old_path: Optional[str] = None
    try:
        conn = _open_for_command(paths["db"], need_fts=True, need_vec=True, args=args)
        conn.execute("BEGIN")

        # If this URL already exists (non-Local), and --update-existing is set,
        # update the existing record instead of inserting a new one.
        existing_id: Optional[int] = None
        if getattr(args, "update_existing", False) and args.url:
            row = dbmod.get_article_by_source(conn, source_url)
            if row is not None:
                existing_id = int(row["id"])
                old_path = (row["local_file_path"] or "").strip() or None

        if existing_id is not None:
            # Update path: overwrite metadata but keep the same id.
            dbmod.update_article_fields(
                conn,
                existing_id,
                title=title,
                summary=summary,
                tags=tags,
                category=category,
                priority=priority,
            )
            new_id = existing_id
        else:
            new_id = dbmod.insert_article(
                conn,
                title=title,
                source_url=source_url,
                tags=tags,
                summary=summary,
                category=category,
                local_file_path="",
                priority=priority,
                created_at=created_at,
            )

        md_path = article_abspath(paths["articles_dir"], new_id, title)
        md_content = format_markdown_with_metadata(
            article_id=new_id,
            title=title,
            source_url=source_url,
            created_at=created_at,
            category=category,
            tags=tags,
            priority=priority,
            body_markdown=body_md,
        )
        write_markdown(md_path, md_content)

        dbmod.update_article_fields(conn, new_id, local_file_path=md_path)

        # If we updated an existing article and the file path changed,
        # rename the old file to a .bak_<timestamp> suffix so it can be
        # cleaned up safely by external scripts.
        if existing_id is not None and old_path and old_path != md_path and os.path.exists(old_path):
            ts_suffix = now_iso_z().replace(":", "").replace("-", "").replace("T", "").replace("Z", "")
            bak_path = f"{old_path}.bak_{ts_suffix}"
            try:
                os.rename(old_path, bak_path)
            except Exception:
                # Best-effort: if rename fails, we just leave the old file.
                pass

        # Index sync
        try:
            dbmod.upsert_fts(conn, new_id, title, tags, summary)
        except Exception as e:
            sys.stderr.write(f"WARNING: FTS upsert failed: {e}\n")

        embed_on = embedding_enabled()
        if embed_on and summary:
            # Only attempt vec upsert when the vec table actually exists.
            try:
                if dbmod.vec_table_exists(conn):
                    try:
                        emb = get_embedding(summary)
                        blob = floats_to_f32_blob(emb)
                        dbmod.upsert_vec(conn, new_id, blob)
                    except Exception as e:
                        sys.stderr.write(f"WARNING: vec upsert failed: {e}\n")
                else:
                    embed_on = False
            except Exception:
                embed_on = False

        conn.commit()

        out = {
            "id": new_id,
            "title": title,
            "created_at": created_at,
            "category": category,
            "local_file_path": md_path,
            "embedding_enabled": embed_on,
        }
        if args.json:
            _print(out, True)
        else:
            _print(f"id={new_id} title={title}\npath={md_path}", False)
        return 0
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        sys.stderr.write(f"ERROR: ingest failed: {e}\n")
        return 4
    finally:
        if conn is not None:
            conn.close()

def cmd_show(args) -> int:
    paths = _resolve_paths(args)
    db_path = paths["db"]
    if not os.path.exists(db_path):
        sys.stderr.write(f"ERROR: db not found at {db_path}. Check --root/--db or .env configuration.\n")
        return 2
    conn = None
    try:
        conn = _open_for_command(db_path, need_fts=False, need_vec=False, args=args)
        row = dbmod.get_article(conn, int(args.id))
        if not row:
            sys.stderr.write("ERROR: id not found\n")
            return 2

        out = dict(row)
        if args.full:
            p = (row["local_file_path"] or "").strip()
            if p and os.path.exists(p):
                out["content"] = read_markdown(p)
            else:
                out["content"] = ""

        if args.json:
            _print(out, True)
        else:
            txt = (
                f"id: {out.get('id')}\n"
                f"title: {out.get('title')}\n"
                f"source_url: {out.get('source_url')}\n"
                f"created_at: {out.get('created_at')}\n"
                f"modified_at: {out.get('modified_at')}\n"
                f"deleted_at: {out.get('deleted_at')}\n"
                f"category: {out.get('category')}\n"
                f"priority: {out.get('priority')}\n"
                f"tags: {out.get('tags')}\n"
                f"summary:\n{out.get('summary')}\n"
                f"local_file_path: {out.get('local_file_path')}\n"
            )
            if args.full:
                txt += "\n--- CONTENT ---\n" + (out.get("content") or "")
            _print(txt, False)
        return 0
    except Exception as e:
        sys.stderr.write(f"ERROR: show failed: {e}\n")
        return 4
    finally:
        if conn is not None:
            conn.close()

def cmd_export(args) -> int:
    paths = _resolve_paths(args)
    conn = None
    try:
        db_path = paths["db"]
        if not os.path.exists(db_path):
            sys.stderr.write(f"ERROR: db not found at {db_path}. Check --root/--db or .env configuration.\n")
            return 2
        conn = _open_for_command(db_path, need_fts=False, need_vec=False, args=args)
        row = dbmod.get_article(conn, int(args.id))
        if not row:
            sys.stderr.write("ERROR: id not found\n")
            return 2
        out = dict(row)

        content = ""
        p = (row["local_file_path"] or "").strip()
        if p and os.path.exists(p):
            content = read_markdown(p)

        fmt = (args.format or "md").lower()
        out_path = args.out

        if fmt == "json":
            payload = out
            if args.full:
                payload["content"] = content
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        elif fmt == "md":
            if args.full:
                md = content
            else:
                md = (
                    "--- METADATA ---\n"
                    f"id: {out.get('id')}\n"
                    f"title: {out.get('title')}\n"
                    f"source_url: {out.get('source_url')}\n"
                    f"created_at: {out.get('created_at')}\n"
                    f"category: {out.get('category')}\n"
                    f"tags: {out.get('tags')}\n"
                    f"priority: {out.get('priority')}\n"
                    "--- SUMMARY ---\n"
                    f"{out.get('summary')}\n"
                )
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(md)
        else:
            sys.stderr.write("ERROR: export format must be md or json\n")
            return 2

        _print({"ok": True, "out": out_path}, bool(args.json))
        return 0
    except Exception as e:
        sys.stderr.write(f"ERROR: export failed: {e}\n")
        return 4
    finally:
        if conn is not None:
            conn.close()

def cmd_search(args) -> int:
    paths = _resolve_paths(args)
    query = args.query
    embed_on = embedding_enabled()
    conn = None
    try:
        conn = _open_for_command(paths["db"], need_fts=True, need_vec=True, args=args)

        # If DB doesn't have vec table (e.g. first-run or vec disabled),
        # downgrade to FTS-only behavior even if embedding is configured.
        try:
            if not dbmod.vec_table_exists(conn):
                embed_on = False
        except Exception:
            embed_on = False

        def _qvec_blob(q: str) -> bytes:
            emb = get_embedding(q)
            return floats_to_f32_blob(emb)

        filters: Dict[str, Any] = {}
        if args.category:
            filters["category"] = args.category
        if args.tag:
            filters["tag"] = args.tag
        if args.since:
            filters["since"] = args.since
        if args.priority is not None:
            filters["priority"] = args.priority

        res = hybrid_search(
            conn,
            query=query,
            mode=args.mode,
            topk=int(args.topk),
            candidates=int(args.candidates),
            include_deleted=bool(args.include_deleted),
            gen_provider=args.gen_provider,
            llm_keywords=args.llm_keywords,
            embed_enabled=embed_on,
            get_query_vec_blob=_qvec_blob,
            filters=filters,
        )

        if args.json:
            _print(res, True)
        else:
            lines = []
            for x in res:
                lines.append(f"{x['id']:6d}  score={x['score']:.4f}  {x['created_at']}  [{x['category']}]  {x['title']}")
            _print("\n".join(lines) if lines else "(no results)", False)
        return 0
    except Exception as e:
        sys.stderr.write(f"ERROR: search failed: {e}\n")
        return 4
    finally:
        if conn is not None:
            conn.close()

def cmd_update(args) -> int:
    paths = _resolve_paths(args)
    aid = int(args.id)
    conn = None
    try:
        db_path = paths["db"]
        if not os.path.exists(db_path):
            sys.stderr.write(f"ERROR: db not found at {db_path}. Check --root/--db or .env configuration.\n")
            return 2
        conn = _open_for_command(db_path, need_fts=True, need_vec=True, args=args)
        row = dbmod.get_article(conn, aid)
        if not row:
            sys.stderr.write("ERROR: id not found\n")
            return 2

        embed_on = embedding_enabled()
        gen_provider = (args.gen_provider or "openclaw").lower()

        # URL / ID / created_at are read-only by design. We only allow updating
        # title/summary/tags/category/priority (and modified_at implicitly).
        title = (row["title"] or "")
        summary = (row["summary"] or "")
        tags = (row["tags"] or "")
        category = (row["category"] or "")
        priority = int(row["priority"] or 0)

        # Patch
        if args.title is not None:
            title = args.title
        if args.summary is not None:
            summary = truncate_text(args.summary, max_chars=args.max_summary_chars)
        if args.tags is not None:
            tags = comma_join_tags(args.tags)
        if args.category is not None:
            category = args.category
        if args.priority is not None:
            priority = int(args.priority)

        # Regen
        if args.regen:
            regen = args.regen.lower()
            content = ""
            p = (row["local_file_path"] or "").strip()
            if p and os.path.exists(p):
                content = read_markdown(p)
            else:
                content = summary or title

            if regen in ("title", "all"):
                gen = generate_fields(content, hint_title=title or None, provider=gen_provider, max_summary_chars=args.max_summary_chars)
                title = (gen.get("title") or title).strip() or title
            if regen in ("summary", "all"):
                gen = generate_fields(content, hint_title=title or None, provider=gen_provider, max_summary_chars=args.max_summary_chars)
                summary = truncate_text((gen.get("summary") or summary).strip(), max_chars=args.max_summary_chars)
            if regen in ("tags", "all"):
                gen = generate_fields(content, hint_title=title or None, provider=gen_provider, max_summary_chars=args.max_summary_chars)
                tags = comma_join_tags(gen.get("tags") or tags)

        conn.execute("BEGIN")
        dbmod.update_article_fields(conn, aid, title=title, summary=summary, tags=tags, category=category, priority=priority)

        # sync FTS
        try:
            dbmod.upsert_fts(conn, aid, title, tags, summary)
        except Exception as e:
            sys.stderr.write(f"WARNING: fts sync failed: {e}\n")

        # sync vec
        if summary and embed_on:
            try:
                blob = floats_to_f32_blob(get_embedding(summary))
                dbmod.upsert_vec(conn, aid, blob)
            except Exception as e:
                sys.stderr.write(f"WARNING: vec sync failed: {e}\n")
        else:
            try:
                dbmod.delete_vec(conn, aid)
            except Exception:
                pass

        conn.commit()
        _print({"ok": True, "id": aid}, bool(args.json))
        return 0
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        sys.stderr.write(f"ERROR: update failed: {e}\n")
        return 4
    finally:
        if conn is not None:
            conn.close()

def cmd_delete(args) -> int:
    paths = _resolve_paths(args)
    aid = int(args.id)
    conn = None
    try:
        conn = _open_for_command(paths["db"], need_fts=True, need_vec=True, args=args)
        row = dbmod.get_article(conn, aid)
        if not row:
            sys.stderr.write("ERROR: id not found\n")
            return 2

        conn.execute("BEGIN")
        if args.hard:
            try:
                dbmod.delete_fts(conn, aid)
            except Exception:
                pass
            try:
                dbmod.delete_vec(conn, aid)
            except Exception:
                pass
            dbmod.delete_article_row(conn, aid)
            p = (row["local_file_path"] or "").strip()
            if p and os.path.exists(p):
                if args.remove_file:
                    # Explicitly requested immediate removal.
                    try:
                        os.remove(p)
                    except Exception as e:
                        sys.stderr.write(f"WARNING: remove file failed: {e}\n")
                else:
                    # Default: backup-style deletion.
                    ts_suffix = now_iso_z().replace(":", "").replace("-", "").replace("T", "").replace("Z", "")
                    bak_path = f"{p}.bak_deleted_{ts_suffix}"
                    try:
                        os.rename(p, bak_path)
                    except Exception as e:
                        sys.stderr.write(f"WARNING: backup rename failed: {e}\n")
        else:
            # Soft delete: mark deleted_at and move file to a .bak_deleted_ path.
            p = (row["local_file_path"] or "").strip()
            ts_suffix = now_iso_z().replace(":", "").replace("-", "").replace("T", "").replace("Z", "")
            bak_path = None
            if p:
                bak_path = f"{p}.bak_deleted_{ts_suffix}"
                if os.path.exists(p):
                    try:
                        os.rename(p, bak_path)
                    except Exception as e:
                        sys.stderr.write(f"WARNING: backup rename failed: {e}\n")
                    # Even if rename fails, we still proceed with deleted_at flag.
            if bak_path:
                dbmod.update_article_fields(conn, aid, deleted_at=now_iso_z(), local_file_path=bak_path)
            else:
                dbmod.update_article_fields(conn, aid, deleted_at=now_iso_z())
            try:
                dbmod.delete_fts(conn, aid)
            except Exception:
                pass
            try:
                dbmod.delete_vec(conn, aid)
            except Exception:
                pass

        conn.commit()
        _print({"ok": True, "id": aid, "hard": bool(args.hard)}, bool(args.json))
        return 0
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        sys.stderr.write(f"ERROR: delete failed: {e}\n")
        return 4
    finally:
        if conn is not None:
            conn.close()


def cmd_maintenance(args) -> int:
    """KB maintenance wrapper.

    现阶段仍然保留原有的扫描/删除语义，但在非 dry-run 模式下，
    会在最后追加一次 `clawsqlite db vacuum`，使用 plumbing 层做 DB 紧缩。
    """
    paths = _resolve_paths(args)
    articles_dir = paths["articles_dir"]
    db_path = paths["db"]
    dry = bool(args.dry_run)
    # Respect explicit 0; argparse already provides default when missing.
    days = int(args.days)

    now = _dt.datetime.utcnow()
    cutoff = now - _dt.timedelta(days=days)

    # 1) Load DB ids and canonical paths
    conn = None
    db_ids = set()
    id_to_path: Dict[int, str] = {}
    try:
        if os.path.exists(db_path):
            conn = _open_for_command(db_path, need_fts=False, need_vec=False, args=args)
            for row in conn.execute("SELECT id, local_file_path FROM articles"):
                aid = int(row["id"])
                db_ids.add(aid)
                p = (row["local_file_path"] or "").strip()
                if p:
                    id_to_path[aid] = os.path.abspath(p)
    except Exception as e:
        sys.stderr.write(f"WARNING: maintenance: failed to read db ids: {e}\n")
    finally:
        if conn is not None:
            conn.close()

    canonical_paths = set(id_to_path.values())

    orphan_files = []
    bak_files = []
    broken_records = []

    # 2) Scan articles_dir
    if os.path.isdir(articles_dir):
        for root, _, files in os.walk(articles_dir):
            for name in files:
                path = os.path.join(root, name)
                rel = os.path.relpath(path, articles_dir)
                # Detect .bak files; capture leading YYYYMMDD date only
                m_bak = re.search(r"\.bak_(\d{8})", name)
                if m_bak:
                    # Parse timestamp roughly as YYYYMMDD...; we only care about date
                    ts = m_bak.group(1)
                    try:
                        dt = _dt.datetime.strptime(ts[:8], "%Y%m%d")
                        if dt < cutoff:
                            bak_files.append(path)
                    except Exception:
                        # If parse fails, treat as orphan-like candidate
                        orphan_files.append(path)
                    continue

                # Normal markdown files: expect leading numeric id
                m_id = re.match(r"^(\d{1,})__.*\.md$", name)
                if m_id:
                    fid = int(m_id.group(1))
                    if fid not in db_ids or os.path.abspath(path) not in canonical_paths:
                        orphan_files.append(path)
                # else: ignore other files

    # 3) Broken records: DB rows pointing to missing files
    try:
        if os.path.exists(db_path):
            conn = _open_for_command(db_path, need_fts=False, need_vec=False, args=args)
            for row in conn.execute("SELECT id, local_file_path FROM articles"):
                p = (row["local_file_path"] or "").strip()
                if p and not os.path.exists(p):
                    broken_records.append({"id": int(row["id"]), "path": p})
    except Exception as e:
        sys.stderr.write(f"WARNING: maintenance: failed to scan broken records: {e}\n")
    finally:
        if conn is not None:
            conn.close()

    # 4) Report
    result = {
        "orphans": orphan_files,
        "bak_to_delete": bak_files,
        "broken_records": broken_records,
        "days": days,
        "dry_run": dry,
    }

    if dry:
        _print(result, bool(getattr(args, "json", False)))
        return 0

    # 5) Apply deletions
    deleted = []
    for p in orphan_files + bak_files:
        try:
            os.remove(p)
            deleted.append(p)
        except Exception as e:
            sys.stderr.write(f"WARNING: maintenance: failed to delete {p}: {e}\n")

    result["deleted"] = deleted
    _print(result, bool(getattr(args, "json", False)))
    return 0


def cmd_reindex(args) -> int:
    paths = _resolve_paths(args)
    embed_on = embedding_enabled()
    conn = None
    try:
        conn = _open_for_command(paths["db"], need_fts=True, need_vec=True, args=args)

        if args.check:
            out = reindex_mod.check(conn, embed_on=embed_on)
            _print(out, bool(args.json))
            return 0

        if args.fix_missing:
            out = reindex_mod.fix_missing(conn, gen_provider=args.gen_provider, embed_on=embed_on, verbose=bool(args.verbose))
            _print(out, bool(args.json))
            return 0

        if args.rebuild:
            # For now we keep calling the legacy reindex logic, but this
            # is the hook where we can gradually migrate to
            # clawsqlite_plumbing.index primitives via
            # clawsqlite_knowledge.reindex_wrappers.
            out = reindex_mod.rebuild(conn, rebuild_fts=bool(args.fts), rebuild_vec=bool(args.vec), embed_on=embed_on)
            _print(out, bool(args.json))
            return 0 if not out.get("errors") else 4

        sys.stderr.write("ERROR: reindex requires one of --check/--fix-missing/--rebuild\n")
        return 2

    except Exception as e:
        sys.stderr.write(f"ERROR: reindex failed: {e}\n")
        return 4
    finally:
        if conn is not None:
            conn.close()

def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        default=None,
        help=(
            "Root dir. Priority: CLI --root > $CLAWKB_ROOT > $CLAWKB_ROOT_FALLBACK > "
            "<cwd>/clawkb_data."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite db path. Priority: CLI --db > $CLAWKB_DB > <root>/clawkb.sqlite3",
    )
    parser.add_argument(
        "--articles-dir",
        default=None,
        help="Articles markdown dir. Priority: CLI --articles-dir > $CLAWKB_ARTICLES_DIR > <root>/articles",
    )
    parser.add_argument(
        "--tokenizer-ext",
        default=None,
        help="Tokenizer extension path. Default: /usr/local/lib/libsimple.so or $CLAWKB_TOKENIZER_EXT",
    )
    parser.add_argument(
        "--vec-ext",
        default=None,
        help="vec0 extension path. Default: auto-discover or $CLAWKB_VEC_EXT",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clawkb", description="OpenClaw knowledge base CLI (SQLite + FTS5 + sqlite-vec).")
    # Also accept common flags before subcommand
    _add_common_flags(p)

    sub = p.add_subparsers(dest="cmd", required=True)

    # ingest
    sp = sub.add_parser("ingest", help="Ingest a URL or a text into the KB")
    _add_common_flags(sp)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", default=None, help="URL to ingest")
    g.add_argument("--text", default=None, help="Raw text content to ingest")
    sp.add_argument("--title", default=None, help="Title override")
    sp.add_argument("--summary", default=None, help="Summary override (long summary)")
    sp.add_argument("--tags", default=None, help="Tags override (comma-separated)")
    sp.add_argument("--category", default="", help="Category, e.g. web/github/story")
    sp.add_argument("--priority", default=0, type=int, help="Priority (0 default)")
    sp.add_argument("--gen-provider", default="openclaw", choices=["openclaw", "llm", "off"], help="Field generator provider")
    sp.add_argument("--max-summary-chars", default=1200, type=int, help="Hard limit for summary length (chars)")
    sp.add_argument("--scrape-cmd", default=None, help="Scraper command for URL ingest. Or env CLAWKB_SCRAPE_CMD")
    sp.add_argument("--update-existing", action="store_true", help="If URL exists, refresh that record instead of inserting a new one")
    sp.set_defaults(func=cmd_ingest)

    # search
    sp = sub.add_parser("search", help="Search the KB (fts/vec/hybrid)")
    _add_common_flags(sp)
    sp.add_argument("query", help="Query text")
    sp.add_argument("--mode", default="hybrid", choices=["hybrid", "fts", "vec"], help="Search mode")
    sp.add_argument("--topk", default=10, type=int, help="Number of results to return")
    sp.add_argument("--candidates", default=80, type=int, help="Candidate pool size before final ranking")
    sp.add_argument("--llm-keywords", default="auto", choices=["auto", "on", "off"], help="Keyword expansion policy for FTS")
    sp.add_argument("--gen-provider", default="openclaw", choices=["openclaw", "llm", "off"], help="Keyword generator provider (used when llm-keywords=auto/on)")
    sp.add_argument("--category", default=None, help="Filter by category")
    sp.add_argument("--tag", default=None, help="Filter by tag substring")
    sp.add_argument("--since", default=None, help="Filter created_at >= since (ISO, e.g. 2026-03-01T00:00:00Z)")
    sp.add_argument("--priority", default=None, help="Priority filter, e.g. eq:0, gt:0, ge:1")
    sp.add_argument("--include-deleted", action="store_true", help="Include deleted items")
    sp.set_defaults(func=cmd_search)

    # show
    sp = sub.add_parser("show", help="Show one record")
    _add_common_flags(sp)
    sp.add_argument("--id", required=True, help="Article id")
    sp.add_argument("--full", action="store_true", help="Include markdown content")
    sp.set_defaults(func=cmd_show)

    # export
    sp = sub.add_parser("export", help="Export one record to file")
    _add_common_flags(sp)
    sp.add_argument("--id", required=True, help="Article id")
    sp.add_argument("--format", default="md", choices=["md", "json"], help="Export format")
    sp.add_argument("--out", required=True, help="Output file path")
    sp.add_argument("--full", action="store_true", help="Export full markdown content")
    sp.set_defaults(func=cmd_export)

    # update
    sp = sub.add_parser("update", help="Update one record (patch or regen)")
    _add_common_flags(sp)
    sp.add_argument("--id", required=True, help="Article id")
    sp.add_argument("--title", default=None, help="Patch: new title")
    sp.add_argument("--summary", default=None, help="Patch: new summary")
    sp.add_argument("--tags", default=None, help="Patch: new tags (comma-separated)")
    sp.add_argument("--category", default=None, help="Patch: new category")
    sp.add_argument("--priority", default=None, type=int, help="Patch: new priority")
    sp.add_argument("--regen", default=None, choices=["title", "summary", "tags", "embedding", "all"], help="Regenerate fields")
    sp.add_argument("--gen-provider", default="openclaw", choices=["openclaw", "llm", "off"], help="Generator provider for regen")
    sp.add_argument("--max-summary-chars", default=1200, type=int, help="Hard limit for summary length (chars)")
    sp.set_defaults(func=cmd_update)

    # delete
    sp = sub.add_parser("delete", help="Delete one record (soft by default)")
    _add_common_flags(sp)
    sp.add_argument("--id", required=True, help="Article id")
    sp.add_argument("--hard", action="store_true", help="Hard delete (remove db row)")
    sp.add_argument("--remove-file", action="store_true", help="When hard delete, permanently remove markdown file (no backup)")
    sp.set_defaults(func=cmd_delete)

    # reindex
    sp = sub.add_parser("reindex", help="Maintenance: check/fix/rebuild")
    _add_common_flags(sp)
    sp.add_argument("--check", action="store_true", help="Check missing fields and index status")
    sp.add_argument("--fix-missing", action="store_true", help="Fill missing fields and index rows")
    sp.add_argument("--rebuild", action="store_true", help="Rebuild indexes")
    sp.add_argument("--fts", action="store_true", help="With --rebuild: rebuild FTS index")
    sp.add_argument("--vec", action="store_true", help="With --rebuild: rebuild vec index (requires embedding enabled)")
    sp.add_argument("--gen-provider", default="openclaw", choices=["openclaw", "llm", "off"], help="Generator provider for fix-missing")
    sp.set_defaults(func=cmd_reindex)

    # maintenance / gc
    sp = sub.add_parser("maintenance", help="Maintenance: prune orphan/backup files and check paths")
    _add_common_flags(sp)
    sp.add_argument("action", choices=["prune", "gc"], help="Maintenance action (prune=gc)")
    sp.add_argument("--days", type=int, default=3, help="Backup retention in days (for .bak_ files)")
    sp.add_argument("--dry-run", action="store_true", help="Dry run: only report, do not delete")
    sp.set_defaults(func=cmd_maintenance)

    return p

def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))

if __name__ == "__main__":
    raise SystemExit(main())
