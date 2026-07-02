# -*- coding: utf-8 -*-
"""
Search logic for clawsqlite knowledge: vec / fts / hybrid.
"""
from __future__ import annotations

import math
import os
import struct
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import db as dbmod
from .embed import floats_to_f32_blob, l2_normalize
from .utils import (
    build_fts_query_from_keywords,
    has_jieba_for_tags,
    tag_exact_match_bonus,
    tag_match_score,
    parse_iso,
)


def _normalize_vec_distance(distance: float) -> float:
    """Fallback: convert L2 distance to a score in (0,1], higher is better."""
    d = max(0.0, float(distance))
    base = 1.0 / (1.0 + d)
    k = 30.0
    x = base - 0.5
    return 1.0 / (1.0 + math.exp(-k * x))


def _rank_score(rank: int, total: int) -> float:
    if total <= 0:
        return 0.0
    # 1.0 for best rank, down to ~0.0 for worst.
    return max(0.0, (total - rank) / total)


# Default weights for modes with embedding (mode1/mode3):
# semantic(vec) + FTS + tag + priority + recency
_DEFAULT_SCORE_WEIGHTS: Dict[str, float] = {
    "vec": 0.45,
    "fts": 0.25,
    "tag": 0.15,
    "priority": 0.03,
    "recency": 0.02,
}

# Default weights for modes without embedding (mode2/mode4):
# FTS + lexical tag + priority + recency
_DEFAULT_TEXT_SCORE_WEIGHTS: Dict[str, float] = {
    "fts": 0.60,
    "tag": 0.25,
    "priority": 0.08,
    "recency": 0.07,
}

_DEFAULT_SCORE_WEIGHTS_BY_MODE: Dict[str, Dict[str, float]] = {
    "mode1": dict(_DEFAULT_SCORE_WEIGHTS),
    "mode2": dict(_DEFAULT_TEXT_SCORE_WEIGHTS),
    "mode3": dict(_DEFAULT_SCORE_WEIGHTS),
    "mode4": dict(_DEFAULT_TEXT_SCORE_WEIGHTS),
}

_SCORE_WEIGHTS_WARNED: set[str] = set()

# Fraction of the tag channel weight allocated to semantic (vector) tag
# matching when embeddings are enabled. The remaining portion is used for
# lexical tag matching. When embeddings are disabled, tag scoring falls
# back to pure lexical behavior regardless of this setting.
_TAG_VEC_FRACTION_DEFAULT = 0.7

# Log-compression strength for lexical tag scores. Larger alpha means
# stronger compression of mid/high scores; alpha=0 disables compression.
_TAG_FTS_LOG_ALPHA_DEFAULT = 5.0


def _warn_weight_once(env_name: str, msg: str) -> None:
    if env_name in _SCORE_WEIGHTS_WARNED:
        return
    _SCORE_WEIGHTS_WARNED.add(env_name)
    sys.stderr.write(msg)


def _parse_weight_text(
    text: str,
    *,
    keys: Sequence[str],
    aliases: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, float]]:
    aliases = aliases or {}
    key_set = set(keys)
    parts = [p.strip() for p in text.split(",") if p.strip()]
    tmp: Dict[str, float] = {}

    for part in parts:
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        raw_key = k.strip().lower()
        key = aliases.get(raw_key, raw_key)
        if key not in key_set:
            continue
        try:
            val = float(v)
        except Exception:
            continue
        if val < 0:
            continue
        tmp[key] = val

    if set(tmp.keys()) != key_set:
        return None

    total = sum(tmp.values())
    if total <= 0:
        return None

    return {k: (tmp[k] / total) for k in keys}


def _score_weights_for_mode(mode_name: str) -> Dict[str, float]:
    """Resolve final score weights for capability mode.

    Env override priority:
    1) CLAWSQLITE_SCORE_WEIGHTS_MODE1..MODE4 (mode-specific)
    2) Legacy compatibility:
       - mode1/mode3 -> CLAWSQLITE_SCORE_WEIGHTS
       - mode2/mode4 -> CLAWSQLITE_SCORE_WEIGHTS_TEXT
    """
    m = (mode_name or "mode3").strip().lower()
    defaults = dict(_DEFAULT_SCORE_WEIGHTS_BY_MODE.get(m, _DEFAULT_SCORE_WEIGHTS))
    keys = list(defaults.keys())

    aliases: Dict[str, str] = {
        "content": "vec",
        "semantic": "vec",
        "tags": "tag",
    }

    env_candidates = [f"CLAWSQLITE_SCORE_WEIGHTS_{m.upper()}"]
    if m in ("mode1", "mode3"):
        env_candidates.append("CLAWSQLITE_SCORE_WEIGHTS")
    else:
        env_candidates.append("CLAWSQLITE_SCORE_WEIGHTS_TEXT")

    for env_name in env_candidates:
        raw = (os.environ.get(env_name) or "").strip()
        if not raw:
            continue
        parsed = _parse_weight_text(raw, keys=keys, aliases=aliases)
        if parsed is None:
            _warn_weight_once(
                env_name,
                (
                    f"NEXT: {env_name} is invalid or incomplete; "
                    f"expected keys: {','.join(keys)}. "
                    "Using default weights for current search mode.\n"
                ),
            )
            continue
        return parsed

    return defaults


def _score_weights_from_env() -> Dict[str, float]:
    """Backward-compatible helper used by tests/importers.

    This returns the embedding-mode weight profile (mode1/mode3 family).
    """
    return _score_weights_for_mode("mode1")


def _tag_vec_fraction() -> float:
    text = os.environ.get("CLAWSQLITE_TAG_VEC_FRACTION", "").strip()
    if not text:
        return _TAG_VEC_FRACTION_DEFAULT
    try:
        val = float(text)
    except Exception:
        return _TAG_VEC_FRACTION_DEFAULT
    if val < 0.0:
        return 0.0
    if val > 1.0:
        return 1.0
    return val


def _tag_lex_log_compress(x: float) -> float:
    """Apply log compression to lexical tag scores in [0,1].

    f(x) = ln(1 + alpha * x) / ln(1 + alpha)

    where alpha is controlled by CLAWSQLITE_TAG_FTS_LOG_ALPHA
    (default: 5.0). alpha<=0 disables compression.
    """
    alpha_text = os.environ.get("CLAWSQLITE_TAG_FTS_LOG_ALPHA", "").strip()
    if alpha_text:
        try:
            alpha = float(alpha_text)
        except Exception:
            alpha = _TAG_FTS_LOG_ALPHA_DEFAULT
    else:
        alpha = _TAG_FTS_LOG_ALPHA_DEFAULT

    if alpha <= 0.0:
        return max(0.0, min(1.0, float(x)))

    x = max(0.0, min(1.0, float(x)))
    return math.log(1.0 + alpha * x) / math.log(1.0 + alpha)


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except Exception:
        return default
    if val < lo:
        return lo
    if val > hi:
        return hi
    return val


def _search_query_tag_limits() -> Tuple[int, int]:
    min_k = _env_int("CLAWSQLITE_SEARCH_QUERY_TAG_MIN", 8, lo=1, hi=64)
    max_k = _env_int("CLAWSQLITE_SEARCH_QUERY_TAG_MAX", 12, lo=1, hi=64)
    if min_k > max_k:
        min_k = max_k
    return min_k, max_k


def _normalize_keywords(keywords: Sequence[str], *, max_k: int) -> List[str]:
    out: List[str] = []
    seen = set()
    for kw in keywords:
        s = str(kw or "").strip()
        if not s:
            continue
        low = s.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(s)
        if len(out) >= max_k:
            break
    return out


def _f32_blob_to_list(blob: Any) -> Optional[List[float]]:
    if blob is None:
        return None
    if isinstance(blob, memoryview):
        b = blob.tobytes()
    elif isinstance(blob, bytearray):
        b = bytes(blob)
    elif isinstance(blob, bytes):
        b = blob
    else:
        try:
            b = bytes(blob)
        except Exception:
            return None
    if not b or (len(b) % 4) != 0:
        return None
    n = len(b) // 4
    try:
        vals = struct.unpack("<" + "f" * n, b)
    except Exception:
        return None
    return [float(x) for x in vals]


def _cosine01(query_vec: Sequence[float], doc_vec: Sequence[float]) -> float:
    if not query_vec or not doc_vec:
        return 0.0
    if len(query_vec) != len(doc_vec):
        return 0.0

    dot = 0.0
    qn = 0.0
    dn = 0.0
    for q, d in zip(query_vec, doc_vec):
        fq = float(q)
        fd = float(d)
        dot += fq * fd
        qn += fq * fq
        dn += fd * fd

    if qn <= 1e-12 or dn <= 1e-12:
        return 0.0

    cos = dot / (math.sqrt(qn) * math.sqrt(dn))
    if cos < -1.0:
        cos = -1.0
    elif cos > 1.0:
        cos = 1.0
    return (cos + 1.0) * 0.5


def _cosine_score_from_blob(query_vec: Sequence[float], blob: Any) -> Optional[float]:
    doc_vec = _f32_blob_to_list(blob)
    if doc_vec is None:
        return None
    if len(doc_vec) != len(query_vec):
        return None
    return _cosine01(query_vec, doc_vec)


def _fetch_vec_blob_map(conn, table: str, ids: Sequence[int]) -> Dict[int, Any]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    sql = f"SELECT id, embedding FROM {table} WHERE id IN ({placeholders})"
    try:
        rows = conn.execute(sql, list(ids)).fetchall()
    except Exception:
        return {}
    out: Dict[int, Any] = {}
    for r in rows:
        try:
            aid = int(r["id"])
        except Exception:
            continue
        out[aid] = r["embedding"]
    return out


def _tag_lex_raw_score(query_keywords: List[str], tags_csv: str) -> float:
    if has_jieba_for_tags():
        return tag_match_score(query_keywords, tags_csv)
    return tag_exact_match_bonus(query_keywords, tags_csv)


def _tag_lex_candidates(
    conn,
    query_keywords: List[str],
    *,
    limit: int,
    include_deleted: bool,
) -> List[Tuple[int, float]]:
    """Recall extra candidates by lexical tag overlap."""
    if not query_keywords:
        return []

    like_terms: List[str] = []
    seen = set()
    for kw in query_keywords:
        s = kw.strip().lower()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        like_terms.append(f"%{s}%")
        if len(like_terms) >= 12:
            break

    if not like_terms:
        return []

    where_like = " OR ".join(["lower(coalesce(tags,'')) LIKE ?" for _ in like_terms])
    if include_deleted:
        sql = f"SELECT id, tags FROM articles WHERE ({where_like}) ORDER BY created_at DESC LIMIT ?"
        params = list(like_terms) + [max(100, limit * 4)]
    else:
        sql = (
            "SELECT id, tags FROM articles "
            "WHERE deleted_at IS NULL AND (" + where_like + ") "
            "ORDER BY created_at DESC LIMIT ?"
        )
        params = list(like_terms) + [max(100, limit * 4)]

    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []

    scored: List[Tuple[int, float]] = []
    for r in rows:
        aid = int(r["id"])
        raw = _tag_lex_raw_score(query_keywords, r["tags"] or "")
        if raw <= 0.0:
            continue
        scored.append((aid, _tag_lex_log_compress(raw)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


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
    get_query_embedding,  # callable returning List[float]; can raise
    filters: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Return list of search results dicts.
    """
    mode = (mode or "hybrid").lower()
    gen_provider = (gen_provider or "openclaw").lower()
    llm_keywords = (llm_keywords or "auto").lower()
    max_candidates = min(max(1, int(candidates or 80)), 200)

    vec_hits: List[Tuple[int, float]] = []
    fts_hits: List[Tuple[int, float]] = []
    tag_vec_hits: List[Tuple[int, float]] = []

    # Determine whether to run vec/fts channels from CLI mode.
    run_vec = mode in ("hybrid", "vec") and bool(embed_enabled)
    run_fts = mode in ("hybrid", "fts")

    # Query understanding: query_refine + query_tags.
    from .generator import generate_search_query_plan

    qmin, qmax = _search_query_tag_limits()
    use_llm = (gen_provider == "llm") and (llm_keywords != "off")
    plan_provider = "llm" if use_llm else "openclaw"
    query_plan = generate_search_query_plan(
        query,
        provider=plan_provider,
        min_k=qmin,
        max_k=qmax,
    )

    query_refine = str(query_plan.get("query_refine") or query or "").strip()
    if not query_refine:
        return []

    query_tags = _normalize_keywords(query_plan.get("query_tags", []), max_k=qmax)
    query_tags = dbmod.fts_normalize_keywords(conn, query_tags, max_k=qmax)
    if not query_tags:
        query_tags = dbmod.fts_keywords_for_query(conn, query_refine, max_k=qmax)
        query_tags = dbmod.fts_normalize_keywords(conn, query_tags, max_k=qmax)

    # Capability mode (4 tiers) is determined by active vec channel + whether
    # query understanding used LLM successfully.
    llm_used = bool(query_plan.get("used_llm"))
    if run_vec and llm_used:
        capability_mode = "mode1"
    elif (not run_vec) and llm_used:
        capability_mode = "mode2"
    elif run_vec and (not llm_used):
        capability_mode = "mode3"
    else:
        capability_mode = "mode4"

    query_vec: Optional[List[float]] = None
    query_tag_vec: Optional[List[float]] = None

    # Collect vector candidates.
    if run_vec:
        query_vec = l2_normalize(get_query_embedding(query_refine))
        qblob = floats_to_f32_blob(query_vec)
        vec_hits = dbmod.vec_knn(
            conn,
            qblob,
            k=max_candidates,
            include_deleted=include_deleted,
        )

        tag_text = " ".join(query_tags).strip() or query_refine
        query_tag_vec = l2_normalize(get_query_embedding(tag_text))
        qblob_tag = floats_to_f32_blob(query_tag_vec)
        tag_vec_hits = dbmod.tag_vec_knn(
            conn,
            qblob_tag,
            k=max_candidates,
            include_deleted=include_deleted,
        )

    # FTS channel uses query_refine text.
    if run_fts:
        fts_keywords = dbmod.fts_keywords_for_query(conn, query_refine, max_k=max(10, qmax))
        if not fts_keywords:
            fts_keywords = query_tags
        fts_keywords = dbmod.fts_normalize_keywords(conn, fts_keywords, max_k=max(10, qmax))
        fts_query = build_fts_query_from_keywords(fts_keywords)
        if fts_query:
            fts_hits = dbmod.fts_search(
                conn,
                fts_query,
                limit=max_candidates,
                include_deleted=include_deleted,
            )

    # Lexical tag channel can recall extra candidates in hybrid/fts paths.
    tag_lex_hits: List[Tuple[int, float]] = []
    if mode in ("hybrid", "fts"):
        tag_lex_hits = _tag_lex_candidates(
            conn,
            query_tags,
            limit=max_candidates,
            include_deleted=include_deleted,
        )

    # Candidate union
    cand_ids: List[int] = []
    seen = set()

    for aid, _d in vec_hits:
        if aid in seen:
            continue
        seen.add(aid)
        cand_ids.append(aid)
        if len(cand_ids) >= max_candidates:
            break

    for aid, _s in fts_hits:
        if aid in seen:
            continue
        seen.add(aid)
        cand_ids.append(aid)
        if len(cand_ids) >= max_candidates:
            break

    for aid, _d in tag_vec_hits:
        if len(cand_ids) >= max_candidates:
            break
        if aid in seen:
            continue
        seen.add(aid)
        cand_ids.append(aid)

    for aid, _tag in tag_lex_hits:
        if len(cand_ids) >= max_candidates:
            break
        if aid in seen:
            continue
        seen.add(aid)
        cand_ids.append(aid)

    if not cand_ids:
        return []

    # Fetch article rows
    placeholders = ",".join("?" for _ in cand_ids)
    sql = f"SELECT * FROM articles WHERE id IN ({placeholders})"
    rows = conn.execute(sql, cand_ids).fetchall()

    # Build maps
    vec_map = {aid: dist for aid, dist in vec_hits}
    tag_vec_map = {aid: dist for aid, dist in tag_vec_hits}
    fts_rank_map: Dict[int, int] = {}
    for idx, (aid, _bm25) in enumerate(fts_hits):
        fts_rank_map[aid] = idx  # 0 is best
    total_fts = len(fts_hits)

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

    filtered_ids = [int(r["id"]) for r in filtered]
    vec_blob_map: Dict[int, Any] = {}
    tag_vec_blob_map: Dict[int, Any] = {}
    if run_vec:
        vec_blob_map = _fetch_vec_blob_map(conn, "articles_vec", filtered_ids)
        tag_vec_blob_map = _fetch_vec_blob_map(conn, "articles_tag_vec", filtered_ids)

    # Score
    results: List[Dict[str, Any]] = []
    for r in filtered:
        aid = int(r["id"])

        dist = vec_map.get(aid)
        vec_score = 0.0
        if run_vec and query_vec is not None:
            cos_score = _cosine_score_from_blob(query_vec, vec_blob_map.get(aid))
            if cos_score is not None:
                vec_score = cos_score
            elif dist is not None:
                vec_score = _normalize_vec_distance(dist)

        if aid in fts_rank_map:
            fts_score = _rank_score(fts_rank_map[aid], total_fts)
        else:
            fts_score = 0.0

        tag_lex_raw = _tag_lex_raw_score(query_tags, r["tags"] or "")
        tag_lex_score = _tag_lex_log_compress(tag_lex_raw)

        tag_vec_dist = tag_vec_map.get(aid)
        tag_vec_score = 0.0
        if run_vec and query_tag_vec is not None:
            tag_cos = _cosine_score_from_blob(query_tag_vec, tag_vec_blob_map.get(aid))
            if tag_cos is not None:
                tag_vec_score = tag_cos
            elif tag_vec_dist is not None:
                tag_vec_score = _normalize_vec_distance(tag_vec_dist)

        if run_vec:
            frac = _tag_vec_fraction()
            tag_score = frac * tag_vec_score + (1.0 - frac) * tag_lex_score
        else:
            tag_score = tag_lex_score

        bonus_priority = 1.0 if int(r["priority"] or 0) > 0 else 0.0

        # Recency bonus: rank by created_at among candidates.
        dt = parse_iso(r["created_at"] or "")
        ts = dt.timestamp() if dt else 0.0

        results.append(
            {
                "id": aid,
                "source_title": r["source_title"] or "",
                "generated_title": r["generated_title"] or "",
                "category": r["category"] or "",
                "created_at": r["created_at"] or "",
                "tags": r["tags"] or "",
                "summary": r["summary"] or "",
                "local_file_path": r["local_file_path"] or "",
                "priority": int(r["priority"] or 0),
                "_mode": capability_mode,
                "_query_refine": query_refine,
                "_query_tags": query_tags,
                "_vec_distance": float(dist) if dist is not None else None,
                "_vec_score": vec_score,
                "_fts_score": fts_score,
                "_tag_score": tag_score,
                "_tag_lex_score": tag_lex_score,
                "_tag_vec_score": tag_vec_score,
                "_priority_bonus": bonus_priority,
                "_ts": ts,
            }
        )

    # Normalize recency bonus by timestamp within results.
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
    weights = _score_weights_for_mode(capability_mode)
    for x in results:
        final = (
            weights.get("vec", 0.0) * x["_vec_score"]
            + weights.get("fts", 0.0) * x["_fts_score"]
            + weights.get("tag", 0.0) * x["_tag_score"]
            + weights.get("priority", 0.0) * x["_priority_bonus"]
            + weights.get("recency", 0.0) * x["_recency_bonus"]
        )
        x["score"] = float(final)

    results.sort(key=lambda d: d["score"], reverse=True)
    return results[:topk]
