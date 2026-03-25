# -*- coding: utf-8 -*-
"""
Search logic for clawsqlite knowledge: vec / fts / hybrid.
"""
from __future__ import annotations

import os
import math
from typing import Any, Dict, List, Optional, Tuple

from . import db as dbmod
from .utils import (
    build_fts_query_from_keywords,
    has_jieba_for_tags,
    tag_exact_match_bonus,
    tag_match_score,
    parse_iso,
)

def _normalize_vec_distance(distance: float) -> float:
    # Convert L2 distance to a score in (0,1], higher is better.
    # This is simple and stable.
    return 1.0 / (1.0 + max(0.0, float(distance)))

def _rank_score(rank: int, total: int) -> float:
    if total <= 0:
        return 0.0
    # 1.0 for best rank, down to ~0.0 for worst.
    return max(0.0, (total - rank) / total)


_DEFAULT_SCORE_WEIGHTS: Dict[str, float] = {
    "vec": 0.55,
    "fts": 0.25,
    "tag": 0.15,
    "priority": 0.03,
    "recency": 0.02,
}


def _score_weights_from_env() -> Dict[str, float]:
    """Return final score weights (vec/fts/tag/priority/recency).

    Users can override the defaults via CLAWSQLITE_SCORE_WEIGHTS, e.g.::

        CLAWSQLITE_SCORE_WEIGHTS=vec=0.55,fts=0.25,tag=0.15,priority=0.03,recency=0.02

    The env override must provide all five keys; otherwise it is ignored
    and defaults are used. Values are normalized to sum to 1.0.
    """
    text = os.environ.get("CLAWSQLITE_SCORE_WEIGHTS", "").strip()
    if not text:
        return dict(_DEFAULT_SCORE_WEIGHTS)

    parts = [p.strip() for p in text.split(",") if p.strip()]
    tmp: Dict[str, float] = {}
    for part in parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = k.strip().lower()
        if key not in _DEFAULT_SCORE_WEIGHTS:
            continue
        try:
            val = float(v)
        except Exception:
            continue
        if val < 0:
            continue
        tmp[key] = val

    # Require all keys to be present; partial overrides are ignored to
    # keep behavior predictable.
    if set(tmp.keys()) != set(_DEFAULT_SCORE_WEIGHTS.keys()):
        return dict(_DEFAULT_SCORE_WEIGHTS)

    total = sum(tmp.values())
    if total <= 0:
        return dict(_DEFAULT_SCORE_WEIGHTS)

    return {k: (tmp[k] / total) for k in _DEFAULT_SCORE_WEIGHTS.keys()}

def hybrid_search(
    conn,
    *,
    query: str,
    mode: str,
    topk: int,
    candidates: int,
    include_deleted: bool,
    gen_provider: str,
    llm_keywords: str,
    embed_enabled: bool,
    get_query_vec_blob,  # callable returning bytes; can raise
    filters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Return list of search results dicts.
    """
    mode = (mode or "hybrid").lower()
    llm_keywords = (llm_keywords or "auto").lower()

    vec_hits: List[Tuple[int, float]] = []
    fts_hits: List[Tuple[int, float]] = []

    # Determine whether to run vec/fts channels
    run_vec = mode in ("hybrid", "vec") and embed_enabled
    run_fts = mode in ("hybrid", "fts")

    # Collect vec candidates
    if run_vec:
        qblob = get_query_vec_blob(query)
        vec_hits = dbmod.vec_knn(conn, qblob, k=min(max(1, candidates), 200), include_deleted=include_deleted)

    # Build FTS query keywords from the natural-language query using the
    # same heuristics as tag generation (TextRank + optional semantic
    # centrality), then normalize them for FTS.
    from .generator import generate_keywords_for_search

    raw_keywords = generate_keywords_for_search(query, provider="openclaw", max_k=10)
    keywords = dbmod.fts_normalize_keywords(conn, raw_keywords, max_k=10)
    fts_query = build_fts_query_from_keywords(keywords)

    if run_fts and fts_query:
        fts_hits = dbmod.fts_search(conn, fts_query, limit=min(max(1, candidates), 200), include_deleted=include_deleted)

        # auto keyword expansion if results are too few
        if llm_keywords == "auto" and len(fts_hits) < min(5, topk):
            try:
                from .generator import generate_keywords_for_search
                kws2 = generate_keywords_for_search(query, provider=gen_provider, max_k=12)
                kws2 = dbmod.fts_normalize_keywords(conn, kws2, max_k=12)
                fts_query2 = build_fts_query_from_keywords(kws2)
                if fts_query2 and fts_query2 != fts_query:
                    fts_hits = dbmod.fts_search(conn, fts_query2, limit=min(max(1, candidates), 200), include_deleted=include_deleted)
                    keywords = kws2
            except Exception:
                # Keep original
                pass

    # Candidate union
    cand_ids = []
    seen = set()
    for aid, _d in vec_hits:
        if aid in seen:
            continue
        seen.add(aid)
        cand_ids.append(aid)
        if len(cand_ids) >= candidates:
            break
    for aid, _s in fts_hits:
        if aid in seen:
            continue
        seen.add(aid)
        cand_ids.append(aid)
        if len(cand_ids) >= candidates:
            break

    if not cand_ids:
        return []

    # Fetch article rows
    placeholders = ",".join("?" for _ in cand_ids)
    sql = f"SELECT * FROM articles WHERE id IN ({placeholders})"
    rows = conn.execute(sql, cand_ids).fetchall()

    # Build maps
    vec_map = {aid: dist for aid, dist in vec_hits}
    # For FTS, use rank-based score (bm25 scale differs across builds)
    fts_rank_map: Dict[int, int] = {}
    for idx, (aid, _bm25) in enumerate(fts_hits):
        fts_rank_map[aid] = idx  # 0 is best

    total_fts = len(fts_hits)
    total_vec = len(vec_hits)

    # Apply filters in Python (simpler and stable)
    def _pass_filters(r) -> bool:
        if not include_deleted and r["deleted_at"] is not None:
            return False
        cat = filters.get("category")
        if cat and (r["category"] or "") != cat:
            return False
        tag = filters.get("tag")
        if tag:
            tags = (r["tags"] or "")
            if tag.lower() not in tags.lower().replace("，", ","):
                return False
        since = filters.get("since")
        if since:
            dt = parse_iso(r["created_at"] or "")
            dt_since = parse_iso(since)
            if dt and dt_since and dt < dt_since:
                return False
        pr = filters.get("priority")
        if pr is not None:
            try:
                pr_val = int(r["priority"])
            except Exception:
                pr_val = 0
            # priority filter supports forms: "gt:0", "ge:1", "eq:0"
            if isinstance(pr, str) and ":" in pr:
                op, val = pr.split(":", 1)
                try:
                    val_i = int(val)
                except Exception:
                    val_i = 0
                if op == "gt" and not (pr_val > val_i):
                    return False
                if op == "ge" and not (pr_val >= val_i):
                    return False
                if op == "lt" and not (pr_val < val_i):
                    return False
                if op == "le" and not (pr_val <= val_i):
                    return False
                if op == "eq" and not (pr_val == val_i):
                    return False
            else:
                try:
                    val_i = int(pr)
                except Exception:
                    val_i = 0
                if pr_val != val_i:
                    return False
        return True

    filtered = [r for r in rows if _pass_filters(r)]
    if not filtered:
        return []

    # Score
    results: List[Dict[str, Any]] = []
    now_dt = parse_iso("1970-01-01T00:00:00Z")  # placeholder; we use recency by relative order only
    for r in filtered:
        aid = int(r["id"])
        dist = vec_map.get(aid, None)
        vec_score = _normalize_vec_distance(dist) if dist is not None else 0.0

        if aid in fts_rank_map:
            fts_score = _rank_score(fts_rank_map[aid], total_fts)
        else:
            fts_score = 0.0

        # Use richer tag match scoring only when jieba is available (tags
        # are ordered by importance). Without jieba we fall back to a
        # simple 0/1 exact-match bonus to avoid overfitting noisy order.
        if has_jieba_for_tags():
            tag_score = tag_match_score(keywords, r["tags"] or "")
        else:
            tag_score = tag_exact_match_bonus(keywords, r["tags"] or "")
        bonus_priority = 1.0 if int(r["priority"] or 0) > 0 else 0.0

        # Recency bonus: rank by created_at among candidates
        # We convert created_at to timestamp; missing -> 0.
        dt = parse_iso(r["created_at"] or "")
        ts = dt.timestamp() if dt else 0.0

        results.append(
            {
                "id": aid,
                "title": r["title"] or "",
                "category": r["category"] or "",
                "created_at": r["created_at"] or "",
                "tags": r["tags"] or "",
                "summary": r["summary"] or "",
                "local_file_path": r["local_file_path"] or "",
                "priority": int(r["priority"] or 0),
                "_vec_distance": float(dist) if dist is not None else None,
                "_vec_score": vec_score,
                "_fts_score": fts_score,
                "_tag_score": tag_score,
                "_priority_bonus": bonus_priority,
                "_ts": ts,
            }
        )

    # Normalize recency bonus by timestamp within results
    if results:
        ts_vals = [x["_ts"] for x in results]
        mn, mx = min(ts_vals), max(ts_vals)
        for x in results:
            if mx > mn:
                rec = (x["_ts"] - mn) / (mx - mn)
            else:
                rec = 0.0
            x["_recency_bonus"] = rec

    # Final score
    weights = _score_weights_from_env()
    for x in results:
        final = (
            weights["vec"] * x["_vec_score"]
            + weights["fts"] * x["_fts_score"]
            + weights["tag"] * x["_tag_score"]
            + weights["priority"] * x["_priority_bonus"]
            + weights["recency"] * x["_recency_bonus"]
        )
        x["score"] = float(final)

    results.sort(key=lambda d: d["score"], reverse=True)
    return results[:topk]
