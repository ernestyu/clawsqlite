# -*- coding: utf-8 -*-
"""
Knowledge CLI entrypoint (clawsqlite_knowledge).

Implements:
- ingest / reindex / search / show / update / delete / export / doctor

Note on CLI ergonomics:
- Knowledge commands load the project-root clawsqlite.toml before resolving paths.
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
from .utils import now_iso_z, truncate_text, comma_join_tags, resolve_interest_params, load_project_env
from .config import ConfigError, KnowledgeConfig, apply_config_env, load_knowledge_config
from .storage import (
    ensure_dir,
    article_abspath,
    format_markdown_with_metadata,
    read_markdown,
    write_markdown,
)
from .generator import generate_fields
from .embed import embedding_enabled, get_embedding, floats_to_f32_blob, _embedding_missing_keys, l2_normalize
from .scraper import scrape_url
from .search import hybrid_search
from . import reindex as reindex_mod
from . import interest as interest_mod

# Plumbing layer (generic db/index/fs helpers)
try:
    from clawsqlite_plumbing import db_cli as _db_plumbing_cli
except Exception:  # pragma: no cover
    _db_plumbing_cli = None

_WARNED_FTS_FALLBACK = False


CONFIG_TEMPLATE = """# clawsqlite knowledge configuration.
# This file is owned by the knowledge app. Plumbing commands do not read it.

[knowledge]
root = "{root}"
db = "knowledge.sqlite3"
articles_dir = "articles"

[ingest]
require_llm = true
require_embedding = true
summary_mode = "llm"
summary_target_chars = 800
tags_mode = "llm"
fallback = "fail"

[llm]
base_url = "https://llm.example.com/v1"
model = "your-small-llm"
api_key = ""
timeout_seconds = 90
# Character-budget approximation for the model context. The generator
# subtracts prompt_reserved_chars before deciding whether to chunk.
context_window_chars = 24000
prompt_reserved_chars = 4000
chunk_overlap_chars = 500

[embedding]
base_url = "https://embed.example.com/v1"
model = "your-embedding-model"
api_key = ""
dim = 1024
timeout_seconds = 300
content = "summary"

[scraper]
# cmd = "node /path/to/scrape.js"
"""


def _get_config(args, *, require: bool = True) -> KnowledgeConfig:
    cfg = getattr(args, "_knowledge_config", None)
    if cfg is not None:
        return cfg
    cfg = load_knowledge_config(require=require)
    apply_config_env(cfg)
    setattr(args, "_knowledge_config", cfg)
    return cfg


def _resolve_paths(args) -> Dict[str, str]:
    """Resolve paths from the root clawsqlite.toml."""

    cfg = _get_config(args)
    return {"root": cfg.root, "db": cfg.db, "articles_dir": cfg.articles_dir}

def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    else:
        if isinstance(obj, str):
            sys.stdout.write(obj + "\n")
        else:
            sys.stdout.write(str(obj) + "\n")

def _extract_markdown_body(content: str) -> str:
    """Extract markdown body from our metadata+markdown format."""
    if not content:
        return ""
    marker = "--- MARKDOWN ---"
    idx = content.find(marker)
    if idx != -1:
        return content[idx + len(marker) :].lstrip("\r\n")
    return content

def _open_for_command(db_path: str, *, need_fts: bool, need_vec: bool, args) -> Any:
    tokenizer_ext = getattr(args, "tokenizer_ext", None)
    vec_ext = getattr(args, "vec_ext", None)
    conn = dbmod.open_db(db_path, need_fts=need_fts, need_vec=need_vec, tokenizer_ext=tokenizer_ext, vec_ext=vec_ext)
    if need_fts:
        _maybe_warn_fts_fallback(conn)
    return conn

def _maybe_warn_fts_fallback(conn) -> None:
    global _WARNED_FTS_FALLBACK
    if _WARNED_FTS_FALLBACK:
        return
    warn = dbmod.fts_fallback_warning(conn)
    if not warn:
        return
    sys.stderr.write("WARNING: " + warn["message"] + "\n")
    sys.stderr.write("ERROR_KIND: " + warn["error_kind"] + "\n")
    sys.stderr.write("NEXT: " + warn["next"] + "\n")
    _WARNED_FTS_FALLBACK = True


def cmd_embed_from_summary(args) -> int:
    """Embed article summaries into the vec table.

    This is a knowledge-level wrapper around `clawsqlite embed column` that
    knows the default KB schema:

    - base table: articles
    - id column: id
    - text column: summary
    - vec table: articles_vec
    - default WHERE: undeleted rows with non-empty summary
    """
    from clawsqlite_plumbing import embed_cli as _embed_plumbing_cli  # local import to avoid hard dep at import time

    paths = _resolve_paths(args)
    db_path = paths["db"]

    where = args.where or "deleted_at IS NULL AND summary IS NOT NULL AND trim(summary) != ''"
    argv = [
        "column",
        "--db",
        db_path,
        "--table",
        "articles",
        "--id-col",
        "id",
        "--text-col",
        "summary",
        "--vec-table",
        "articles_vec",
        "--where",
        where,
    ]
    if args.limit is not None:
        argv += ["--limit", str(int(args.limit))]
    if args.offset is not None:
        argv += ["--offset", str(int(args.offset))]

    code = _embed_plumbing_cli.main(argv)
    return int(code)


def cmd_init_config(args) -> int:
    out = getattr(args, "out", None) or "clawsqlite.toml"
    path = os.path.abspath(os.path.expanduser(out))
    if os.path.exists(path) and not getattr(args, "force", False):
        sys.stderr.write(f"ERROR: config already exists at {path}\n")
        sys.stderr.write("NEXT: pass --force to overwrite, or choose --out /path/to/clawsqlite.toml.\n")
        return 2
    root = "./knowledge_data"
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        f.write(CONFIG_TEMPLATE.format(root=root))
    _print({"ok": True, "config_path": path}, bool(getattr(args, "json", False)))
    return 0


def cmd_ingest(args) -> int:
    cfg = _get_config(args)
    policy = cfg.ingest
    paths = _resolve_paths(args)
    ensure_dir(paths["articles_dir"])

    source_url = args.url or "Local"
    category = args.category or ""
    priority = int(args.priority or 0)
    gen_provider = (args.gen_provider or ("llm" if policy.summary_mode == "llm" or policy.tags_mode == "llm" else "openclaw")).lower()
    allow_heuristic = bool(getattr(args, "allow_heuristic", False))
    allow_missing_embedding = bool(getattr(args, "allow_missing_embedding", False))
    max_summary_chars = int(args.max_summary_chars or policy.summary_target_chars)

    if policy.require_llm and gen_provider != "llm" and not allow_heuristic:
        sys.stderr.write("ERROR: LLM generation is required by clawsqlite.toml.\n")
        sys.stderr.write("ERROR_KIND: llm_required\n")
        sys.stderr.write("NEXT: use the default LLM path, or explicitly pass --allow-heuristic for degraded ingest.\n")
        return 2

    # 1) Fetch content
    hint_title = args.title
    body_md = ""
    if args.url:
        try:
            t, md = scrape_url(args.url, scrape_cmd=args.scrape_cmd or cfg.scraper.cmd or None)
            if t and not hint_title:
                hint_title = t
            body_md = md
        except Exception as e:
            sys.stderr.write(f"ERROR: scrape failed: {e}\n")
            sys.stderr.write(
                "NEXT: install the 'clawfetch' skill from ClawHub and set [scraper].cmd "
                "in clawsqlite.toml, or pass --scrape-cmd \"<your-scraper> <url>\" for a debug override.\n"
            )
            return 3
    else:
        body_md = args.text

    # 2) Fields
    tags = args.tags or ""
    summary = (args.summary or "").strip()
    summary_generated = False
    title = hint_title or ""

    generation_provider = gen_provider
    generation_quality = "manual" if gen_provider == "off" else ""
    summary_model = ""
    tags_model = ""
    content_type = "note" if args.text else "web_article"
    key_claims_json = "[]"
    entities_json = "[]"

    if gen_provider != "off":
        need_gen = bool(policy.require_llm) or (not title) or (not summary) or (not tags and not args.tags)
        if need_gen:
            try:
                gen = generate_fields(
                    body_md,
                    hint_title=title or None,
                    provider=gen_provider,
                    max_summary_chars=max_summary_chars,
                    allow_heuristic=allow_heuristic or not policy.require_llm,
                    llm_context_window_chars=cfg.llm.context_window_chars,
                    llm_prompt_reserved_chars=cfg.llm.prompt_reserved_chars,
                    llm_chunk_overlap_chars=cfg.llm.chunk_overlap_chars,
                    llm_timeout_seconds=cfg.llm.timeout_seconds,
                )
                if not title:
                    title = (gen.get("title") or "").strip()
                if policy.require_llm or not summary:
                    summary = (gen.get("summary") or "").strip()
                    summary_generated = True
                if policy.require_llm or (not tags and not args.tags):
                    tags = comma_join_tags(gen.get("tags"))
                generation_quality = str(gen.get("generation_quality") or ("llm" if gen_provider == "llm" else "heuristic"))
                if generation_quality == "llm":
                    summary_model = cfg.llm.model
                    tags_model = cfg.llm.model
                content_type = str(gen.get("content_type") or content_type)
                key_claims_json = json.dumps(gen.get("key_claims") or [], ensure_ascii=False)
                entities_json = json.dumps(gen.get("entities") or [], ensure_ascii=False)
            except Exception as e:
                if policy.require_llm and not allow_heuristic:
                    sys.stderr.write(f"ERROR: LLM field generation failed: {e}\n")
                    sys.stderr.write("ERROR_KIND: llm_generation_failed\n")
                    sys.stderr.write("NEXT: fix [llm] config/API output, or explicitly pass --allow-heuristic for degraded ingest.\n")
                    return 4
                sys.stderr.write(f"WARNING: generate_fields failed: {e}\n")

    title = title.strip() or "untitled"
    if summary_generated:
        summary = summary.strip()
    else:
        summary = truncate_text(summary, max_chars=max_summary_chars)
    tags = comma_join_tags(tags)

    if policy.require_llm and generation_quality != "llm" and not allow_heuristic:
        sys.stderr.write("ERROR: strict ingest requires LLM-generated summary and tags.\n")
        sys.stderr.write("ERROR_KIND: llm_required\n")
        sys.stderr.write("NEXT: configure [llm].base_url/model/api_key in clawsqlite.toml, or pass --allow-heuristic.\n")
        return 2

    if policy.require_embedding and not allow_missing_embedding and not embedding_enabled():
        missing = ", ".join(_embedding_missing_keys())
        sys.stderr.write(f"ERROR: embedding is required by clawsqlite.toml; missing {missing}.\n")
        sys.stderr.write("ERROR_KIND: embedding_required\n")
        sys.stderr.write("NEXT: configure [embedding].base_url/model/api_key/dim in clawsqlite.toml, or pass --allow-missing-embedding.\n")
        return 2

    created_at = now_iso_z()
    created_at_for_md = created_at
    embedding_model = cfg.embedding.model if embedding_enabled() else ""
    embedding_dim: Optional[int] = cfg.embedding.dim if cfg.embedding.dim > 0 else None

    conn = None
    old_path: Optional[str] = None
    try:
        conn = _open_for_command(paths["db"], need_fts=True, need_vec=True, args=args)
        conn.execute("BEGIN")

        # If this URL already exists (non-Local), require an explicit refresh
        # flag so agents get a predictable error instead of a UNIQUE failure.
        existing_id: Optional[int] = None
        if args.url:
            row = dbmod.get_article_by_source(conn, source_url)
            if row is not None and getattr(args, "update_existing", False):
                existing_id = int(row["id"])
                old_path = (row["local_file_path"] or "").strip() or None
                existing_created_at = (row["created_at"] or "").strip()
                if existing_created_at:
                    created_at_for_md = existing_created_at
            elif row is not None:
                sys.stderr.write(f"ERROR: source_url already exists: id={int(row['id'])}\n")
                sys.stderr.write(
                    "NEXT: rerun with --update-existing to refresh this URL, "
                    "or use update/delete on the existing id.\n"
                )
                conn.rollback()
                return 2

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
                generation_provider=generation_provider,
                generation_quality=generation_quality,
                summary_model=summary_model,
                tags_model=tags_model,
                embedding_model=embedding_model,
                embedding_dim=embedding_dim,
                ingest_status="ok",
                ingest_error="",
                content_type=content_type,
                key_claims=key_claims_json,
                entities=entities_json,
                config_path=cfg.config_path,
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
                generation_provider=generation_provider,
                generation_quality=generation_quality,
                summary_model=summary_model,
                tags_model=tags_model,
                embedding_model=embedding_model,
                embedding_dim=embedding_dim,
                ingest_status="ok",
                ingest_error="",
                content_type=content_type,
                key_claims=key_claims_json,
                entities=entities_json,
                config_path=cfg.config_path,
            )

        md_path = article_abspath(paths["articles_dir"], new_id, title)
        md_content = format_markdown_with_metadata(
            article_id=new_id,
            title=title,
            source_url=source_url,
            created_at=created_at_for_md,
            category=category,
            tags=tags,
            priority=priority,
            body_markdown=body_md,
            summary=summary,
            generation_quality=generation_quality,
            summary_model=summary_model,
            tags_model=tags_model,
            embedding_model=embedding_model,
            content_type=content_type,
            key_claims=key_claims_json,
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
            dbmod.upsert_fts(conn, new_id, title, tags, summary, body_md)
        except Exception as e:
            sys.stderr.write(f"WARNING: FTS upsert failed: {e}\n")

        embed_on = embedding_enabled()
        if embed_on and (summary or tags):
            # Only attempt vec upsert when the vec table actually exists.
            try:
                if dbmod.vec_table_exists(conn):
                    try:
                        if summary:
                            emb = l2_normalize(get_embedding(summary, timeout=cfg.embedding.timeout_seconds))
                            blob = floats_to_f32_blob(emb)
                            dbmod.upsert_vec(conn, new_id, blob)
                        else:
                            dbmod.delete_vec(conn, new_id)
                        if tags:
                            emb_tag = l2_normalize(get_embedding(tags, timeout=cfg.embedding.timeout_seconds))
                            blob_tag = floats_to_f32_blob(emb_tag)
                            dbmod.upsert_tag_vec(conn, new_id, blob_tag)
                        else:
                            dbmod.delete_tag_vec(conn, new_id)
                    except Exception as e:
                        if policy.require_embedding and not allow_missing_embedding:
                            raise
                        sys.stderr.write(f"WARNING: vec/tag_vec upsert failed: {e}\n")
                else:
                    if policy.require_embedding and not allow_missing_embedding:
                        raise RuntimeError("vec index not available (vec0 extension not loaded)")
                    embed_on = False
            except Exception as e:
                if policy.require_embedding and not allow_missing_embedding:
                    raise RuntimeError(f"embedding/vec sync failed: {e}")
                embed_on = False
        elif policy.require_embedding and not allow_missing_embedding:
            raise RuntimeError("embedding is required but summary/tags are empty or embedding is disabled")

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
        sys.stderr.write("NEXT: run 'clawsqlite knowledge ingest --help' to check required options and paths.\n")
        return 4
    finally:
        if conn is not None:
            conn.close()

def cmd_show(args) -> int:
    paths = _resolve_paths(args)
    db_path = paths["db"]
    if not os.path.exists(db_path):
        sys.stderr.write(f"ERROR: db not found at {db_path}. Check project-root clawsqlite.toml.\n")
        sys.stderr.write("NEXT: set [knowledge].root/[knowledge].db in clawsqlite.toml, "
                         "or run an ingest command first to initialize the DB.\n")
        return 2
    conn = None
    try:
        conn = _open_for_command(db_path, need_fts=False, need_vec=False, args=args)
        row = dbmod.get_article(conn, int(args.id))
        if not row:
            sys.stderr.write("ERROR: id not found\n")
            sys.stderr.write("NEXT: use 'clawsqlite knowledge search ... --json' to locate a valid id before calling show.\n")
            return 2

        # Best-effort usage tracking: atomic upsert with view_count += 1
        try:
            now = now_iso_z()
            conn.execute(
                """
INSERT INTO article_usage(article_id, view_count, first_viewed_at, last_viewed_at)
VALUES(?, 1, ?, ?)
ON CONFLICT(article_id) DO UPDATE SET
  view_count = article_usage.view_count + 1,
  last_viewed_at = excluded.last_viewed_at
""",
                (int(args.id), now, now),
            )
            conn.commit()
        except Exception:
            # Telemetry only; never break show.
            pass

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
            sys.stderr.write(f"ERROR: db not found at {db_path}. Check project-root clawsqlite.toml.\n")
            return 2
        conn = _open_for_command(db_path, need_fts=False, need_vec=False, args=args)
        row = dbmod.get_article(conn, int(args.id))
        if not row:
            sys.stderr.write("ERROR: id not found\n")
            sys.stderr.write("NEXT: use 'clawsqlite knowledge search ... --json' to locate a valid id before calling export.\n")
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
            sys.stderr.write("NEXT: use --format md or --format json (default is md).\n")
            return 2

        _print({"ok": True, "out": out_path}, bool(args.json))
        return 0
    except Exception as e:
        sys.stderr.write(f"ERROR: export failed: {e}\n")
        sys.stderr.write("NEXT: run 'clawsqlite knowledge export --help' to verify options and output path.\n")
        return 4
    finally:
        if conn is not None:
            conn.close()

def cmd_doctor(args) -> int:
    from .doctor import run_doctor

    return run_doctor(
        config=getattr(args, "_knowledge_config", None),
        check_embedding=bool(getattr(args, "check_embedding", False)),
        check_llm=bool(getattr(args, "check_llm", False)),
    )


def _format_search_results(results: List[Dict[str, Any]], *, explain: bool) -> List[Dict[str, Any]]:
    """Return a stable JSON contract for agents, with optional diagnostics."""

    out: List[Dict[str, Any]] = []
    for row in results:
        public = {k: v for k, v in row.items() if not k.startswith("_")}
        if explain:
            public["explain"] = {
                "mode": row.get("_mode"),
                "query_refine": row.get("_query_refine"),
                "query_tags": row.get("_query_tags"),
                "vec_distance": row.get("_vec_distance"),
                "scores": {
                    "vec": row.get("_vec_score"),
                    "fts": row.get("_fts_score"),
                    "tag": row.get("_tag_score"),
                    "tag_lex": row.get("_tag_lex_score"),
                    "tag_vec": row.get("_tag_vec_score"),
                    "priority": row.get("_priority_bonus"),
                    "recency": row.get("_recency_bonus"),
                },
            }
        out.append(public)
    return out


def cmd_search(args) -> int:
    paths = _resolve_paths(args)
    query = args.query
    embed_on = embedding_enabled()
    conn = None
    try:
        conn = _open_for_command(paths["db"], need_fts=True, need_vec=True, args=args)

        # Determine embedding availability and vec index readiness.
        missing_keys = _embedding_missing_keys()
        vec_ready = False
        try:
            vec_ready = dbmod.vec_table_exists(conn)
        except Exception:
            vec_ready = False

        embed_on = (not missing_keys) and vec_ready

        def _embed_reason() -> str:
            reasons = []
            if missing_keys:
                reasons.append("missing " + ", ".join(missing_keys))
            if not vec_ready:
                reasons.append("vec index not available (vec0 extension not loaded)")
            return "; ".join(reasons) if reasons else "embedding not available"

        # Vec-only mode requires embeddings; fail fast with a clear NEXT hint.
        if (args.mode or "hybrid").lower() == "vec" and not embed_on:
            reason = _embed_reason()
            sys.stderr.write(f"ERROR: vector search requires embeddings; {reason}.\n")
            sys.stderr.write(
                "NEXT: configure [embedding].base_url/model/api_key/dim in clawsqlite.toml, "
                "and ensure vec0 is available.\n"
            )
            return 2

        # Hybrid mode auto-falls back to FTS, but tell the user.
        if (args.mode or "hybrid").lower() == "hybrid" and not embed_on:
            reason = _embed_reason()
            sys.stderr.write(
                "NEXT: embedding is not available (" + reason + "); "
                "falling back to FTS-only. Configure embedding + vec0 for better results.\n"
            )

        def _qvec(q: str) -> List[float]:
            return l2_normalize(get_embedding(q))

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
            get_query_embedding=_qvec,
            filters=filters,
        )

        if args.json:
            _print(_format_search_results(res, explain=bool(getattr(args, "explain", False))), True)
        else:
            lines = []
            for x in res:
                lines.append(f"{x['id']:6d}  score={x['score']:.4f}  {x['created_at']}  [{x['category']}]  {x['title']}")
            _print("\n".join(lines) if lines else "(no results)", False)
        return 0
    except Exception as e:
        sys.stderr.write(f"ERROR: search failed: {e}\n")
        sys.stderr.write("NEXT: run 'clawsqlite knowledge search --help' to check mode/filters, and verify embedding env vars when using hybrid/vec.\n")
        return 4
    finally:
        if conn is not None:
            conn.close()

def cmd_update(args) -> int:
    cfg = _get_config(args)
    paths = _resolve_paths(args)
    aid = int(args.id)
    conn = None
    try:
        db_path = paths["db"]
        if not os.path.exists(db_path):
            sys.stderr.write(f"ERROR: db not found at {db_path}. Check project-root clawsqlite.toml.\n")
            sys.stderr.write("NEXT: set [knowledge].root/[knowledge].db in clawsqlite.toml, "
                             "or run an ingest command first to initialize the DB.\n")
            return 2
        conn = _open_for_command(db_path, need_fts=True, need_vec=True, args=args)
        row = dbmod.get_article(conn, aid)
        if not row:
            sys.stderr.write("ERROR: id not found\n")
            sys.stderr.write("NEXT: use 'clawsqlite knowledge search ... --json' to locate a valid id before calling update.\n")
            return 2

        embed_on = embedding_enabled()
        gen_provider = (args.gen_provider or ("llm" if cfg.ingest.summary_mode == "llm" or cfg.ingest.tags_mode == "llm" else "openclaw")).lower()
        allow_heuristic = bool(getattr(args, "allow_heuristic", False))
        if args.regen and cfg.ingest.require_llm and gen_provider != "llm" and not allow_heuristic:
            sys.stderr.write("ERROR: LLM generation is required by clawsqlite.toml.\n")
            sys.stderr.write("ERROR_KIND: llm_required\n")
            sys.stderr.write("NEXT: use the default LLM path, or explicitly pass --allow-heuristic for degraded update.\n")
            return 2
        max_summary_chars = int(args.max_summary_chars or cfg.ingest.summary_target_chars)

        # URL / ID / created_at are read-only by design. We only allow updating
        # title/summary/tags/category/priority (and modified_at implicitly).
        title = (row["title"] or "")
        summary = (row["summary"] or "")
        summary_before = summary
        tags = (row["tags"] or "")
        tags_before = tags
        category = (row["category"] or "")
        priority = int(row["priority"] or 0)

        # Patch
        if args.title is not None:
            title = args.title
        if args.summary is not None:
            summary = truncate_text(args.summary, max_chars=max_summary_chars)
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

            gen_cache = None
            def _gen_once():
                nonlocal gen_cache
                if gen_cache is None:
                    gen_cache = generate_fields(
                        content,
                        hint_title=title or None,
                        provider=gen_provider,
                        max_summary_chars=max_summary_chars,
                        allow_heuristic=allow_heuristic or not cfg.ingest.require_llm,
                        llm_context_window_chars=cfg.llm.context_window_chars,
                        llm_prompt_reserved_chars=cfg.llm.prompt_reserved_chars,
                        llm_chunk_overlap_chars=cfg.llm.chunk_overlap_chars,
                        llm_timeout_seconds=cfg.llm.timeout_seconds,
                    )
                return gen_cache

            if regen in ("title", "all"):
                gen = _gen_once()
                title = (gen.get("title") or title).strip() or title
            if regen in ("summary", "all"):
                gen = _gen_once()
                summary = (gen.get("summary") or summary).strip()
            if regen in ("tags", "all"):
                gen = _gen_once()
                tags = comma_join_tags(gen.get("tags") or tags)

        # Sync markdown file + local_file_path based on updated title.
        ensure_dir(paths["articles_dir"])
        old_path = (row["local_file_path"] or "").strip()
        new_path = article_abspath(paths["articles_dir"], aid, title)
        content = ""
        for candidate in [old_path, new_path]:
            if candidate and os.path.exists(candidate):
                try:
                    content = read_markdown(candidate)
                    break
                except Exception:
                    content = ""
        body_md = _extract_markdown_body(content) if content else (summary or title)

        # If the filename changes, keep a backup of the old file.
        if old_path and old_path != new_path and os.path.exists(old_path):
            ts_suffix = now_iso_z().replace(":", "").replace("-", "").replace("T", "").replace("Z", "")
            bak_path = f"{old_path}.bak_{ts_suffix}"
            try:
                os.rename(old_path, bak_path)
            except Exception:
                pass

        created_at = (row["created_at"] or "").strip() or now_iso_z()
        source_url = (row["source_url"] or "").strip() or "Local"
        md_content = format_markdown_with_metadata(
            article_id=aid,
            title=title,
            source_url=source_url,
            created_at=created_at,
            category=category,
            tags=tags,
            priority=priority,
            body_markdown=body_md,
        )
        write_markdown(new_path, md_content)

        conn.execute("BEGIN")
        dbmod.update_article_fields(
            conn,
            aid,
            title=title,
            summary=summary,
            tags=tags,
            category=category,
            priority=priority,
            local_file_path=new_path,
        )

        # sync FTS
        try:
            dbmod.upsert_fts(conn, aid, title, tags, summary, body_md)
        except Exception as e:
            sys.stderr.write(f"WARNING: fts sync failed: {e}\n")

        # sync vec
        regen = (args.regen or "").lower().strip()
        need_vec_recompute = regen in ("embedding", "all") or (summary != summary_before)
        need_tag_vec_recompute = regen in ("embedding", "all", "tags") or (tags != tags_before)
        if summary and embed_on and need_vec_recompute:
            try:
                blob = floats_to_f32_blob(l2_normalize(get_embedding(summary)))
                dbmod.upsert_vec(conn, aid, blob)
            except Exception as e:
                sys.stderr.write(f"WARNING: vec sync failed: {e}\n")
        elif not summary or not embed_on:
            try:
                dbmod.delete_vec(conn, aid)
            except Exception:
                pass

        if embed_on and need_tag_vec_recompute:
            tag_text = (tags or "").strip()
            if tag_text:
                try:
                    blob_tag = floats_to_f32_blob(l2_normalize(get_embedding(tag_text)))
                    dbmod.upsert_tag_vec(conn, aid, blob_tag)
                except Exception as e:
                    sys.stderr.write(f"WARNING: tag vec sync failed: {e}\n")
            else:
                try:
                    dbmod.delete_tag_vec(conn, aid)
                except Exception:
                    pass
        elif not embed_on:
            try:
                dbmod.delete_tag_vec(conn, aid)
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
            sys.stderr.write("NEXT: use 'clawsqlite knowledge search ... --json' to locate a valid id before calling delete.\n")
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
            try:
                dbmod.delete_tag_vec(conn, aid)
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
            try:
                dbmod.delete_tag_vec(conn, aid)
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
    """Knowledge maintenance wrapper.

    语义保持不变：
    - `days` 控制 `.bak_YYYYMMDD` 备份保留；
    - 报告 `orphans` / `bak_to_delete` / `broken_records`；
    - 非 dry-run 情况下删除这些文件 + VACUUM DB。

    目前由于 schema 中只有绝对路径 `local_file_path`，这里暂时
    继续用现有逻辑扫描文件，而不是直接调用 fs plumbing 的
    `list-orphans/gc`。后续如果引入 `relpath` 列，可以再切到
    `clawsqlite fs`。
    """

    paths = _resolve_paths(args)
    articles_dir = paths["articles_dir"]
    db_path = paths["db"]
    dry = bool(args.dry_run)
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

    orphan_files: List[str] = []
    bak_files: List[str] = []
    broken_records: List[Dict[str, Any]] = []

    # 2) Scan articles_dir
    if os.path.isdir(articles_dir):
        for root, _, files in os.walk(articles_dir):
            for name in files:
                path = os.path.join(root, name)
                # Detect .bak files; capture leading YYYYMMDD date only
                m_bak = re.search(r"\.bak(?:_deleted)?_(\d{8})", name)
                if m_bak:
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

    # 6) DB VACUUM via plumbing
    if _db_plumbing_cli is not None and os.path.exists(db_path):
        try:
            _db_plumbing_cli.main(["vacuum", "--db", db_path])
            result["vacuum_ran"] = True
        except Exception as e:
            sys.stderr.write(f"WARNING: maintenance: db vacuum via plumbing failed: {e}\n")

    _print(result, bool(getattr(args, "json", False)))
    return 0


def cmd_reindex(args) -> int:
    cfg = _get_config(args)
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
            gen_provider = (args.gen_provider or ("llm" if cfg.ingest.summary_mode == "llm" or cfg.ingest.tags_mode == "llm" else "openclaw")).lower()
            allow_heuristic = bool(getattr(args, "allow_heuristic", False))
            if cfg.ingest.require_llm and gen_provider != "llm" and not allow_heuristic:
                sys.stderr.write("ERROR: LLM generation is required by clawsqlite.toml.\n")
                sys.stderr.write("ERROR_KIND: llm_required\n")
                sys.stderr.write("NEXT: use the default LLM path, or explicitly pass --allow-heuristic for degraded reindex.\n")
                return 2
            out = reindex_mod.fix_missing(
                conn,
                gen_provider=gen_provider,
                embed_on=embed_on,
                max_summary_chars=cfg.ingest.summary_target_chars,
                allow_heuristic=allow_heuristic or not cfg.ingest.require_llm,
                llm_context_window_chars=cfg.llm.context_window_chars,
                llm_prompt_reserved_chars=cfg.llm.prompt_reserved_chars,
                llm_chunk_overlap_chars=cfg.llm.chunk_overlap_chars,
                llm_timeout_seconds=cfg.llm.timeout_seconds,
                verbose=bool(args.verbose),
            )
            _print(out, bool(args.json))
            return 0

        if args.rebuild:
            # Knowledge FTS includes Markdown body text stored in files, so it
            # must be rebuilt by the knowledge layer rather than generic
            # plumbing that only reads base-table columns.
            #
            # Vec rebuild semantics: clear the vec table only. Embedding
            #    recomputation is handled by a separate embedding task/CLI
            #    (e.g. `clawsqlite knowledge embed-from-summary`).
            out = reindex_mod.rebuild(
                conn,
                rebuild_fts=bool(args.fts),
                rebuild_vec=bool(args.vec),
                embed_on=embed_on,
            )
            _print(out, bool(args.json))
            return 0 if not out.get("errors") else 4

        sys.stderr.write("ERROR: reindex requires one of --check/--fix-missing/--rebuild\n")
        sys.stderr.write("NEXT: run 'clawsqlite knowledge reindex --help' to choose an appropriate mode.\n")
        return 2

    except Exception as e:
        sys.stderr.write(f"ERROR: reindex failed: {e}\n")
        sys.stderr.write("NEXT: inspect the error above, then rerun with '--check' first to see current index status.\n")
        return 4
    finally:
        if conn is not None:
            conn.close()


def cmd_rebuild_quality(args) -> int:
    cfg = _get_config(args)
    paths = _resolve_paths(args)
    db_path = paths["db"]
    if not os.path.exists(db_path):
        sys.stderr.write(f"ERROR: db not found at {db_path}. Check project-root clawsqlite.toml.\n")
        return 2
    if cfg.ingest.require_llm and not (cfg.llm.base_url and cfg.llm.model and cfg.llm.resolved_api_key):
        sys.stderr.write("ERROR: rebuild-quality requires configured LLM.\n")
        sys.stderr.write("ERROR_KIND: llm_required\n")
        sys.stderr.write("NEXT: configure [llm].base_url/model/api_key in clawsqlite.toml.\n")
        return 2
    if cfg.ingest.require_embedding and not getattr(args, "allow_missing_embedding", False) and not embedding_enabled():
        sys.stderr.write("ERROR: rebuild-quality requires configured embedding.\n")
        sys.stderr.write("ERROR_KIND: embedding_required\n")
        sys.stderr.write("NEXT: configure [embedding] in clawsqlite.toml, or pass --allow-missing-embedding.\n")
        return 2

    conn = None
    try:
        conn = _open_for_command(db_path, need_fts=True, need_vec=True, args=args)
        clauses = ["deleted_at IS NULL"]
        params: List[Any] = []
        if getattr(args, "id", None):
            clauses.append("id=?")
            params.append(int(args.id))
        else:
            clauses.append("(coalesce(generation_quality,'') != 'llm' OR coalesce(summary_model,'') = '' OR coalesce(tags_model,'') = '')")
        if getattr(args, "since", None):
            clauses.append("created_at >= ?")
            params.append(args.since)
        sql = "SELECT * FROM articles WHERE " + " AND ".join(clauses) + " ORDER BY id ASC"
        if getattr(args, "limit", None):
            sql += " LIMIT ?"
            params.append(int(args.limit))
        rows = conn.execute(sql, params).fetchall()
        ids = [int(r["id"]) for r in rows]
        if getattr(args, "dry_run", False):
            _print({"ok": True, "dry_run": True, "ids": ids, "count": len(ids)}, bool(args.json))
            return 0

        updated: List[int] = []
        errors: List[str] = []
        for r in rows:
            aid = int(r["id"])
            try:
                path = (r["local_file_path"] or "").strip()
                body_md = read_markdown(path) if path and os.path.exists(path) else (r["summary"] or r["title"] or "")
                body_md = _extract_markdown_body(body_md)
                gen = generate_fields(
                    body_md,
                    hint_title=(r["title"] or "") or None,
                    provider="llm",
                    max_summary_chars=cfg.ingest.summary_target_chars,
                    allow_heuristic=False,
                    llm_context_window_chars=cfg.llm.context_window_chars,
                    llm_prompt_reserved_chars=cfg.llm.prompt_reserved_chars,
                    llm_chunk_overlap_chars=cfg.llm.chunk_overlap_chars,
                    llm_timeout_seconds=cfg.llm.timeout_seconds,
                )
                title = (gen.get("title") or r["title"] or "").strip() or "untitled"
                summary = (gen.get("summary") or "").strip()
                tags = comma_join_tags(gen.get("tags"))
                key_claims_json = json.dumps(gen.get("key_claims") or [], ensure_ascii=False)
                entities_json = json.dumps(gen.get("entities") or [], ensure_ascii=False)
                content_type = str(gen.get("content_type") or r["content_type"] or "web_article")

                embedding_model = cfg.embedding.model if embedding_enabled() else ""
                embedding_dim: Optional[int] = cfg.embedding.dim if cfg.embedding.dim > 0 else None
                if embedding_enabled():
                    if not dbmod.vec_table_exists(conn) and cfg.ingest.require_embedding and not getattr(args, "allow_missing_embedding", False):
                        raise RuntimeError("vec index not available (vec0 extension not loaded)")
                    if dbmod.vec_table_exists(conn):
                        dbmod.upsert_vec(conn, aid, floats_to_f32_blob(l2_normalize(get_embedding(summary, timeout=cfg.embedding.timeout_seconds))))
                        dbmod.upsert_tag_vec(conn, aid, floats_to_f32_blob(l2_normalize(get_embedding(tags, timeout=cfg.embedding.timeout_seconds))))
                elif cfg.ingest.require_embedding and not getattr(args, "allow_missing_embedding", False):
                    raise RuntimeError("embedding is required but not configured")

                new_path = article_abspath(paths["articles_dir"], aid, title)
                md_content = format_markdown_with_metadata(
                    article_id=aid,
                    title=title,
                    source_url=(r["source_url"] or "").strip() or "Local",
                    created_at=(r["created_at"] or "").strip() or now_iso_z(),
                    category=r["category"] or "",
                    tags=tags,
                    priority=int(r["priority"] or 0),
                    body_markdown=body_md,
                    summary=summary,
                    generation_quality="llm",
                    summary_model=cfg.llm.model,
                    tags_model=cfg.llm.model,
                    embedding_model=embedding_model,
                    content_type=content_type,
                    key_claims=key_claims_json,
                )
                write_markdown(new_path, md_content)
                dbmod.update_article_fields(
                    conn,
                    aid,
                    title=title,
                    summary=summary,
                    tags=tags,
                    local_file_path=new_path,
                    generation_provider="llm",
                    generation_quality="llm",
                    summary_model=cfg.llm.model,
                    tags_model=cfg.llm.model,
                    embedding_model=embedding_model,
                    embedding_dim=embedding_dim,
                    ingest_status="ok",
                    ingest_error="",
                    content_type=content_type,
                    key_claims=key_claims_json,
                    entities=entities_json,
                    config_path=cfg.config_path,
                )
                dbmod.upsert_fts(conn, aid, title, tags, summary, body_md)
                updated.append(aid)
            except Exception as e:
                errors.append(f"id={aid}: {e}")
                try:
                    dbmod.update_article_fields(conn, aid, ingest_status="failed", ingest_error=str(e))
                except Exception:
                    pass
        conn.commit()
        out = {"ok": not errors, "updated": updated, "errors": errors}
        _print(out, bool(args.json))
        return 0 if not errors else 4
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        sys.stderr.write(f"ERROR: rebuild-quality failed: {e}\n")
        return 4
    finally:
        if conn is not None:
            conn.close()

def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tokenizer-ext",
        default=None,
        help="Tokenizer extension path. Default: /usr/local/lib/libsimple.so or $CLAWSQLITE_TOKENIZER_EXT",
    )
    parser.add_argument(
        "--vec-ext",
        default=None,
        help="vec0 extension path. Default: auto-discover or $CLAWSQLITE_VEC_EXT",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")


def cmd_build_interest_clusters(args) -> int:
    paths = _resolve_paths(args)
    db_path = paths["db"]
    if not os.path.exists(db_path):
        sys.stderr.write(f"ERROR: db not found at {db_path}. Check project-root clawsqlite.toml.\n")
        sys.stderr.write(
            "NEXT: set [knowledge].root/[knowledge].db in clawsqlite.toml, "
            "or run an ingest command first to initialize the DB.\n"
        )
        return 2
    conn = None
    try:
        conn = _open_for_command(db_path, need_fts=False, need_vec=True, args=args)
        params = resolve_interest_params(
            cli_min_size=getattr(args, "min_size", None),
            cli_max_clusters=getattr(args, "max_clusters", None),
            cli_algo=getattr(args, "algo", None),
            cli_tag_weight=getattr(args, "tag_weight", None),
            cli_use_pca=getattr(args, "use_pca", None),
            cli_pca_explained_variance_threshold=getattr(args, "pca_explained_variance_threshold", None),
            cli_kmeans_random_state=getattr(args, "kmeans_random_state", None),
            cli_kmeans_n_init=getattr(args, "kmeans_n_init", None),
            cli_kmeans_max_iter=getattr(args, "kmeans_max_iter", None),
            cli_enable_post_merge=getattr(args, "enable_post_merge", None),
            cli_merge_distance_threshold=getattr(args, "merge_distance_threshold", None),
            cli_hierarchical_distance_threshold=getattr(args, "hierarchical_distance_threshold", None),
            cli_hierarchical_linkage=getattr(args, "hierarchical_linkage", None),
        )
        out = interest_mod.build_interest_clusters(
            conn,
            min_size=int(params["min_size"]),
            max_clusters=int(params["max_clusters"]),
            cluster_algo=str(params["cluster_algo"]),
            tag_weight=float(params["tag_weight"]),
            use_pca=bool(params["use_pca"]),
            pca_explained_variance_threshold=float(params["pca_explained_variance_threshold"]),
            kmeans_random_state=int(params["kmeans_random_state"]),
            kmeans_n_init=int(params["kmeans_n_init"]),
            kmeans_max_iter=int(params["kmeans_max_iter"]),
            enable_post_merge=bool(params["enable_post_merge"]),
            merge_distance_threshold=float(params["merge_distance_threshold"]),
            hierarchical_distance_threshold=float(params["hierarchical_distance_threshold"]),
            hierarchical_linkage=str(params["hierarchical_linkage"]),
        )
        _print(out, bool(getattr(args, "json", False)))
        return 0 if out.get("ok") else 4
    except Exception as e:
        sys.stderr.write(f"ERROR: build-interest-clusters failed: {e}\n")
        return 4
    finally:
        if conn is not None:
            conn.close()


def cmd_inspect_interest_clusters(args) -> int:
    paths = _resolve_paths(args)
    db_path = paths["db"]
    if not os.path.exists(db_path):
        sys.stderr.write(f"ERROR: db not found at {db_path}. Check project-root clawsqlite.toml.\n")
        return 2
    try:
        try:
            from .inspect_interest import inspect_interest_clusters
        except ImportError as e:
            sys.stderr.write("ERROR: inspect-interest-clusters requires extra dependencies (numpy; matplotlib optional).\n")
            sys.stderr.write("NEXT: pip install 'clawsqlite[analysis]'  # numpy\n")
            sys.stderr.write("      pip install 'clawsqlite[analysis,plot]'  # + matplotlib\n")
            sys.stderr.write(f"DETAIL: {e}\n")
            return 2
        inspect_interest_clusters(
            db_path,
            vec_dim=getattr(args, "vec_dim", None),
            no_plot=bool(getattr(args, "no_plot", False)),
        )
        return 0
    except SystemExit as e:
        if isinstance(e.code, str) and e.code.strip():
            sys.stderr.write(e.code.strip() + "\n")
            return 2
        return int(e.code or 1)
    except Exception as e:
        sys.stderr.write(f"ERROR: inspect-interest-clusters failed: {e}\n")
        return 4


def cmd_report_interest(args) -> int:
    paths = _resolve_paths(args)
    db_path = paths["db"]
    if not os.path.exists(db_path):
        sys.stderr.write(f"ERROR: db not found at {db_path}. Check project-root clawsqlite.toml.\n")
        return 2
    try:
        try:
            from .report_interest import run_interest_report
        except ImportError as e:
            sys.stderr.write("ERROR: report-interest requires optional analysis dependencies (numpy; matplotlib/pandoc optional).\n")
            sys.stderr.write("NEXT: install with 'pip install clawsqlite[analysis]' or install numpy in this environment.\n")
            sys.stderr.write(f"DETAIL: {e}\n")
            return 2
        report_dir = run_interest_report(
            db_path,
            days=int(getattr(args, "days", 7) or 7),
            date_from=getattr(args, "date_from", None),
            date_to=getattr(args, "date_to", None),
            vec_dim=getattr(args, "vec_dim", None),
            out_dir=str(getattr(args, "out_dir", "reports") or "reports"),
            lang=getattr(args, "lang", None),
            no_pdf=bool(getattr(args, "no_pdf", False)),
            fmt=getattr(args, "fmt", None),
        )
        sys.stdout.write(f"Report written to {report_dir}\n")
        return 0
    except SystemExit as e:
        return int(e.code or 1)
    except Exception as e:
        sys.stderr.write(f"ERROR: report-interest failed: {e}\n")
        return 4


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clawsqlite knowledge", description="OpenClaw knowledge base CLI (SQLite + FTS5 + sqlite-vec).")
    # Also accept common flags before subcommand
    _add_common_flags(p)

    sub = p.add_subparsers(dest="cmd", required=True)

    # init-config
    sp = sub.add_parser("init-config", help="Create a clawsqlite.toml template for the knowledge app")
    _add_common_flags(sp)
    sp.add_argument("--out", default=None, help="Output config path (default: ./clawsqlite.toml)")
    sp.add_argument("--force", action="store_true", help="Overwrite an existing config file")
    sp.set_defaults(func=cmd_init_config)

    # build-interest-clusters
    sp = sub.add_parser("build-interest-clusters", help="Build interest clusters from existing article embeddings")
    _add_common_flags(sp)
    sp.add_argument("--algo", choices=["kmeans++", "hierarchical"], default=None, help="Clustering backend (default from env or kmeans++)")
    sp.add_argument("--tag-weight", type=float, default=None, help="Weight of tag_vec in interest-vector mix, range [0,1]")
    sp.add_argument("--use-pca", dest="use_pca", action="store_true", default=None, help="Enable PCA before clustering")
    sp.add_argument("--no-pca", dest="use_pca", action="store_false", help="Disable PCA and cluster in original vector space")
    sp.add_argument(
        "--pca-explained-variance-threshold",
        type=float,
        default=None,
        help="PCA cumulative explained variance threshold (e.g. 0.90, 0.95)",
    )
    sp.add_argument("--min-size", "--min-cluster-size", dest="min_size", type=int, default=None, help="Minimum cluster size")
    sp.add_argument("--max-clusters", type=int, default=None, help="Maximum initial clusters (kmeans++)")
    sp.add_argument("--kmeans-random-state", type=int, default=None, help="Random seed for kmeans++")
    sp.add_argument("--kmeans-n-init", type=int, default=None, help="Number of kmeans++ restarts")
    sp.add_argument("--kmeans-max-iter", type=int, default=None, help="Max iterations per kmeans++ run")
    sp.add_argument("--enable-post-merge", dest="enable_post_merge", action="store_true", default=None, help="Enable post-merge of close clusters (kmeans++)")
    sp.add_argument("--disable-post-merge", dest="enable_post_merge", action="store_false", help="Disable post-merge of close clusters (kmeans++)")
    sp.add_argument("--merge-distance-threshold", type=float, default=None, help="Post-merge cosine-distance threshold (kmeans++)")
    sp.add_argument("--hierarchical-distance-threshold", type=float, default=None, help="Distance threshold used to cut hierarchical tree")
    sp.add_argument("--hierarchical-linkage", choices=["average", "complete"], default=None, help="Hierarchical linkage strategy")
    sp.set_defaults(func=cmd_build_interest_clusters)

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
    sp.add_argument("--gen-provider", default=None, choices=["openclaw", "llm", "off"], help="Generator provider override (default from clawsqlite.toml)")
    sp.add_argument("--max-summary-chars", default=None, type=int, help="Summary target/limit override (default from clawsqlite.toml)")
    sp.add_argument("--scrape-cmd", default=None, help="Debug scraper command override for URL ingest (default from clawsqlite.toml)")
    sp.add_argument("--update-existing", action="store_true", help="If URL exists, refresh that record instead of inserting a new one")
    sp.add_argument("--allow-heuristic", action="store_true", help="Explicitly allow heuristic generation when LLM generation is unavailable")
    sp.add_argument("--allow-missing-embedding", action="store_true", help="Explicitly allow ingest without vector embeddings")
    sp.set_defaults(func=cmd_ingest)

    # doctor
    sp = sub.add_parser("doctor", help="Self-check knowledge DB/env, output JSON report")
    _add_common_flags(sp)
    sp.add_argument("--check-llm", action="store_true", help="Reserved for LLM roundtrip checks")
    sp.add_argument("--check-embedding", action="store_true", help="Run embedding roundtrip check")
    sp.set_defaults(func=cmd_doctor)

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
    sp.add_argument("--explain", action="store_true", help="Include query plan and score breakdown in JSON output")
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
    sp.add_argument(
        "--regen",
        default=None,
        choices=["title", "summary", "tags", "embedding", "all"],
        help="Regenerate fields (embedding=refresh vec from summary)",
    )
    sp.add_argument("--gen-provider", default=None, choices=["openclaw", "llm", "off"], help="Generator provider for regen (default from clawsqlite.toml)")
    sp.add_argument("--max-summary-chars", default=None, type=int, help="Summary target/limit override (default from clawsqlite.toml)")
    sp.add_argument("--allow-heuristic", action="store_true", help="Explicitly allow heuristic generation when LLM generation is unavailable")
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
    sp.add_argument("--vec", action="store_true", help="With --rebuild: clear vec index (no embedding)")
    sp.add_argument("--gen-provider", default=None, choices=["openclaw", "llm", "off"], help="Generator provider for fix-missing (default from clawsqlite.toml)")
    sp.add_argument("--allow-heuristic", action="store_true", help="Explicitly allow heuristic generation when LLM generation is unavailable")
    sp.set_defaults(func=cmd_reindex)

    # rebuild-quality
    sp = sub.add_parser("rebuild-quality", help="Regenerate low-quality records with strict LLM generation")
    _add_common_flags(sp)
    sp.add_argument("--id", default=None, help="Rebuild one article id")
    sp.add_argument("--since", default=None, help="Only rebuild records created_at >= since")
    sp.add_argument("--limit", type=int, default=None, help="Maximum records to rebuild")
    sp.add_argument("--dry-run", action="store_true", help="Only list records that would be rebuilt")
    sp.add_argument("--allow-missing-embedding", action="store_true", help="Allow rebuild without vector embeddings")
    sp.set_defaults(func=cmd_rebuild_quality)

    # inspect-interest-clusters (analysis helper)
    sp = sub.add_parser("inspect-interest-clusters", help="Inspect interest cluster radius + PCA scatter plot (requires numpy)")
    _add_common_flags(sp)
    sp.add_argument("--vec-dim", type=int, default=None, help="Embedding dimension (optional, default: CLAWSQLITE_VEC_DIM / auto)")
    sp.add_argument("--no-plot", action="store_true", help="Only print stats, do not generate PNG plot")
    sp.set_defaults(func=cmd_inspect_interest_clusters)

    # report-interest (KB activity / interest clusters report)
    sp = sub.add_parser("report-interest", help="Generate an interest cluster activity report (Markdown + optional PDF)")
    _add_common_flags(sp)
    sp.add_argument("--days", type=int, default=7, help="Lookback window in days (ignored if --from/--to provided)")
    sp.add_argument("--from", dest="date_from", default=None, help="Start date (YYYY-MM-DD)")
    sp.add_argument("--to", dest="date_to", default=None, help="End date (YYYY-MM-DD, exclusive)")
    sp.add_argument("--vec-dim", type=int, default=None, help="Embedding dimension (optional, default: CLAWSQLITE_VEC_DIM / auto)")
    sp.add_argument("--out-dir", default="reports", help="Root directory for reports (default: ./reports)")
    sp.add_argument("--lang", default=None, help="Report language (en/zh). Default: $CLAWSQLITE_REPORT_LANG or en")
    sp.add_argument("--format", dest="fmt", default=None, choices=["md", "html"], help="Additional output format: 'md' (default) or 'html' (also write report.html via pandoc)")
    sp.add_argument("--no-pdf", action="store_true", help="Do not run pandoc to generate PDF")
    sp.set_defaults(func=cmd_report_interest)

    # embed-from-summary (knowledge-level wrapper)
    sp = sub.add_parser("embed-from-summary", help="Embed article summaries into articles_vec via plumbing")
    _add_common_flags(sp)
    sp.add_argument("--where", default=None, help="Optional SQL WHERE clause on articles (default: undeleted with non-empty summary)")
    sp.add_argument("--limit", type=int, default=None, help="Optional LIMIT for batching")
    sp.add_argument("--offset", type=int, default=None, help="Optional OFFSET for batching")
    sp.set_defaults(func=cmd_embed_from_summary)

    # maintenance / gc
    sp = sub.add_parser("maintenance", help="Maintenance: prune orphan/backup files and check paths")
    _add_common_flags(sp)
    sp.add_argument("action", choices=["prune", "gc"], help="Maintenance action (prune=gc)")
    sp.add_argument("--days", type=int, default=3, help="Backup retention in days (for .bak_ files)")
    sp.add_argument("--dry-run", action="store_true", help="Dry run: only report, do not delete")
    sp.set_defaults(func=cmd_maintenance)

    return p

def main(argv: Optional[List[str]] = None) -> int:
    # Load project-level .env for optional low-level tuning knobs. Knowledge
    # runtime config itself should live in clawsqlite.toml.
    try:
        load_project_env()
    except Exception:
        # Best-effort; CLI/env resolution will still work with system env only.
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    require_config = True
    if args.cmd == "init-config":
        require_config = False
    try:
        if args.cmd != "init-config":
            _get_config(args, require=require_config)
    except ConfigError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        sys.stderr.write("ERROR_KIND: config_required\n")
        sys.stderr.write(
            "NEXT: create clawsqlite.toml with 'clawsqlite knowledge init-config', "
            "then run the command from that project root or a directory inside it.\n"
        )
        return 2
    return int(args.func(args))

if __name__ == "__main__":
    raise SystemExit(main())
