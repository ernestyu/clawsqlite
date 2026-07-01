# -*- coding: utf-8 -*-
"""
Utilities for clawsqlite_knowledge.

All code comments are in English by design.
"""
from __future__ import annotations

import os
import re
import json
import unicodedata as _ud
import datetime as _dt
from typing import Any, Dict, List, Optional

try:  # optional jieba for tag scoring heuristics
    import jieba as _jieba_for_tags  # type: ignore
    _JIEBA_FOR_TAGS_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _jieba_for_tags = None  # type: ignore
    _JIEBA_FOR_TAGS_AVAILABLE = False

ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def now_iso_z() -> str:
    """Return current UTC time in ISO 8601 with Z suffix."""
    return _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_interest_params(
    *,
    cli_min_size: Optional[int] = None,
    cli_max_clusters: Optional[int] = None,
    cli_algo: Optional[str] = None,
    cli_tag_weight: Optional[float] = None,
    cli_use_pca: Optional[bool] = None,
    cli_pca_explained_variance_threshold: Optional[float] = None,
    cli_kmeans_random_state: Optional[int] = None,
    cli_kmeans_n_init: Optional[int] = None,
    cli_kmeans_max_iter: Optional[int] = None,
    cli_enable_post_merge: Optional[bool] = None,
    cli_merge_distance_threshold: Optional[float] = None,
    cli_hierarchical_distance_threshold: Optional[float] = None,
    cli_hierarchical_linkage: Optional[str] = None,
    cli_alpha: Optional[float] = None,
) -> Dict[str, Any]:
    """Resolve interest clustering parameters with a clear priority chain."""

    def _parse_bool_like(v: Any, default: bool) -> bool:
        if v is None:
            return default
        s = str(v).strip().lower()
        if s in {"1", "true", "yes", "on", "y"}:
            return True
        if s in {"0", "false", "no", "off", "n"}:
            return False
        return default

    # min_size
    if cli_min_size is not None:
        min_size = cli_min_size
    else:
        env_min = os.environ.get("CLAWSQLITE_INTEREST_MIN_SIZE")
        try:
            min_size = int(env_min) if env_min is not None else 5
        except Exception:
            min_size = 5
    if min_size <= 0:
        min_size = 1

    # max_clusters
    if cli_max_clusters is not None:
        max_clusters = cli_max_clusters
    else:
        env_max = os.environ.get("CLAWSQLITE_INTEREST_MAX_CLUSTERS")
        try:
            max_clusters = int(env_max) if env_max is not None else 16
        except Exception:
            max_clusters = 16
    if max_clusters <= 0:
        max_clusters = 1

    # cluster algorithm
    if cli_algo is not None:
        algo = str(cli_algo).strip().lower()
    else:
        algo = str(os.environ.get("CLAWSQLITE_INTEREST_CLUSTER_ALGO", "kmeans++") or "kmeans++").strip().lower()
    if algo not in {"kmeans++", "hierarchical"}:
        algo = "kmeans++"

    # tag_weight
    if cli_tag_weight is not None:
        tag_weight = float(cli_tag_weight)
    else:
        env_tag_weight = os.environ.get("CLAWSQLITE_INTEREST_TAG_WEIGHT")
        try:
            tag_weight = float(env_tag_weight) if env_tag_weight is not None else 0.75
        except Exception:
            tag_weight = 0.75
    if tag_weight < 0.0:
        tag_weight = 0.0
    if tag_weight > 1.0:
        tag_weight = 1.0

    # use_pca
    if cli_use_pca is not None:
        use_pca = bool(cli_use_pca)
    else:
        use_pca = _parse_bool_like(os.environ.get("CLAWSQLITE_INTEREST_USE_PCA"), True)

    # pca explained variance threshold
    if cli_pca_explained_variance_threshold is not None:
        pca_explained_variance_threshold = float(cli_pca_explained_variance_threshold)
    else:
        env_thr = os.environ.get("CLAWSQLITE_INTEREST_PCA_EXPLAINED_VARIANCE_THRESHOLD")
        try:
            pca_explained_variance_threshold = float(env_thr) if env_thr is not None else 0.95
        except Exception:
            pca_explained_variance_threshold = 0.95
    if pca_explained_variance_threshold <= 0.0:
        pca_explained_variance_threshold = 0.95
    if pca_explained_variance_threshold > 1.0:
        pca_explained_variance_threshold = 1.0

    # kmeans params
    if cli_kmeans_random_state is not None:
        kmeans_random_state = int(cli_kmeans_random_state)
    else:
        env_rs = os.environ.get("CLAWSQLITE_INTEREST_KMEANS_RANDOM_STATE")
        try:
            kmeans_random_state = int(env_rs) if env_rs is not None else 42
        except Exception:
            kmeans_random_state = 42

    if cli_kmeans_n_init is not None:
        kmeans_n_init = int(cli_kmeans_n_init)
    else:
        env_n_init = os.environ.get("CLAWSQLITE_INTEREST_KMEANS_N_INIT")
        try:
            kmeans_n_init = int(env_n_init) if env_n_init is not None else 10
        except Exception:
            kmeans_n_init = 10
    if kmeans_n_init <= 0:
        kmeans_n_init = 1

    if cli_kmeans_max_iter is not None:
        kmeans_max_iter = int(cli_kmeans_max_iter)
    else:
        env_max_iter = os.environ.get("CLAWSQLITE_INTEREST_KMEANS_MAX_ITER")
        try:
            kmeans_max_iter = int(env_max_iter) if env_max_iter is not None else 300
        except Exception:
            kmeans_max_iter = 300
    if kmeans_max_iter <= 0:
        kmeans_max_iter = 1

    if cli_enable_post_merge is not None:
        enable_post_merge = bool(cli_enable_post_merge)
    else:
        enable_post_merge = _parse_bool_like(os.environ.get("CLAWSQLITE_INTEREST_ENABLE_POST_MERGE"), True)

    if cli_merge_distance_threshold is not None:
        merge_distance_threshold = float(cli_merge_distance_threshold)
    else:
        env_merge = os.environ.get("CLAWSQLITE_INTEREST_MERGE_DISTANCE")
        try:
            merge_distance_threshold = float(env_merge) if env_merge is not None else 0.06
        except Exception:
            merge_distance_threshold = 0.06
    if merge_distance_threshold < 0.0:
        merge_distance_threshold = 0.0

    # hierarchical params
    if cli_hierarchical_distance_threshold is not None:
        hierarchical_distance_threshold = float(cli_hierarchical_distance_threshold)
    else:
        env_hier = os.environ.get("CLAWSQLITE_INTEREST_HIERARCHICAL_DISTANCE_THRESHOLD")
        try:
            hierarchical_distance_threshold = float(env_hier) if env_hier is not None else 0.20
        except Exception:
            hierarchical_distance_threshold = 0.20
    if hierarchical_distance_threshold < 0.0:
        hierarchical_distance_threshold = 0.0

    if cli_hierarchical_linkage is not None:
        hierarchical_linkage = str(cli_hierarchical_linkage).strip().lower()
    else:
        hierarchical_linkage = str(os.environ.get("CLAWSQLITE_INTEREST_HIERARCHICAL_LINKAGE", "average") or "average").strip().lower()
    if hierarchical_linkage not in {"average", "complete"}:
        hierarchical_linkage = "average"

    # alpha (for merge distance suggestion)
    if cli_alpha is not None:
        alpha = float(cli_alpha)
    else:
        env_alpha = os.environ.get("CLAWSQLITE_INTEREST_MERGE_ALPHA")
        try:
            alpha = float(env_alpha) if env_alpha is not None else 0.4
        except Exception:
            alpha = 0.4
    if alpha <= 0.0:
        alpha = 0.4

    return {
        "min_size": int(min_size),
        "max_clusters": int(max_clusters),
        "cluster_algo": str(algo),
        "tag_weight": float(tag_weight),
        "use_pca": bool(use_pca),
        "pca_explained_variance_threshold": float(pca_explained_variance_threshold),
        "kmeans_random_state": int(kmeans_random_state),
        "kmeans_n_init": int(kmeans_n_init),
        "kmeans_max_iter": int(kmeans_max_iter),
        "enable_post_merge": bool(enable_post_merge),
        "merge_distance_threshold": float(merge_distance_threshold),
        "hierarchical_distance_threshold": float(hierarchical_distance_threshold),
        "hierarchical_linkage": str(hierarchical_linkage),
        "alpha": float(alpha),
    }


def parse_iso(s: str) -> Optional[_dt.datetime]:
    """Parse ISO 8601 time. Return None if parsing fails."""
    if not s:
        return None
    try:
        if s.endswith("Z"):
            # Python can parse Z only via replace
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return _dt.datetime.fromisoformat(s)
    except Exception:
        return None


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_BASIC_PINYIN_FALLBACK = {
    "这": "zhe",
    "是": "shi",
    "一": "yi",
    "个": "ge",
    "中": "zhong",
    "文": "wen",
    "标": "biao",
    "题": "ti",
    "想": "xiang",
    "要": "yao",
    "搭": "da",
    "建": "jian",
    "人": "ren",
    "卫": "wei",
    "星": "xing",
    "地": "di",
    "面": "mian",
    "站": "zhan",
    "吗": "ma",
    "项": "xiang",
    "目": "mu",
}


def has_cjk(text: str, threshold: int = 2) -> bool:
    """Heuristic CJK detection: True if at least `threshold` CJK chars exist."""
    if not text:
        return False
    return len(_CJK_RE.findall(text)) >= threshold


def slugify(title: str, max_len: int = 60) -> str:
    """Generate a filesystem-safe slug for filenames.

    Strategy:
    - Prefer pinyin for CJK characters when pypinyin is available;
    - Preserve ASCII letters/digits and spaces;
    - Normalize to lowercase, use '-' as word separator;
    - Collapse repeated '-' and trim.

    The goal is to keep filenames ASCII-friendly across platforms while
    maintaining some readability for Chinese titles via pinyin.
    """

    try:
        from pypinyin import lazy_pinyin  # type: ignore
    except Exception:  # pragma: no cover - optional dependency
        lazy_pinyin = None

    s = (title or "").strip()
    if not s:
        return "untitled"

    # Normalize full-width / compatibility forms
    s = _ud.normalize("NFKC", s)

    pieces: List[str] = []
    buf: List[str] = []

    def flush_buf():
        if not buf:
            return
        token = "".join(buf)
        buf.clear()
        token = token.strip()
        if not token:
            return
        pieces.append(token)

    for ch in s:
        if ch.isspace():
            flush_buf()
            continue
        cat = _ud.category(ch)
        if cat[0] in ("L", "N"):
            buf.append(ch)
        else:
            # punctuation / others -> boundary
            flush_buf()

    flush_buf()

    ascii_parts: List[str] = []
    for token in pieces:
        # If token contains CJK, use pinyin. A small built-in fallback keeps
        # filename generation stable in minimal environments without pypinyin.
        if has_cjk(token):
            if lazy_pinyin is not None:
                pinyin_list = lazy_pinyin(token)
            else:
                pinyin_list = [_BASIC_PINYIN_FALLBACK.get(ch, "") for ch in token]
            part = "-".join(x for x in pinyin_list if x)
        else:
            part = token
        # Keep only ASCII letters/digits and spaces; others become '-'
        cleaned = []
        for ch in part:
            if ch.isascii() and (ch.isalnum() or ch in {"-", "_"}):
                cleaned.append(ch.lower())
            elif ch.isspace():
                cleaned.append("-")
            else:
                cleaned.append("-")
        cleaned_s = "".join(cleaned)
        ascii_parts.append(cleaned_s)

    slug = "-".join(ascii_parts)
    # Collapse multiple '-' and trim
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "untitled"
    return slug[:max_len]


def truncate_text(s: str, max_chars: int = 1200) -> str:
    """Hard truncate text with a soft boundary preference."""
    if s is None:
        return ""
    s = s.strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    # Try to cut at a sentence boundary
    for sep in ["\n", "。", ".", "!", "？", "?"]:
        idx = cut.rfind(sep)
        if idx >= max_chars * 0.6:
            return cut[: idx + 1].strip()
    return cut.strip()


def safe_json_load(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except Exception:
        return None


def coalesce(*vals):
    for v in vals:
        if v is not None and v != "":
            return v
    return ""


def comma_join_tags(tags: Any) -> str:
    """Normalize tags to a comma-separated string."""
    if tags is None:
        return ""
    if isinstance(tags, str):
        # Normalize separators
        t = tags.replace("，", ",")
        parts = [p.strip() for p in t.split(",") if p.strip()]
        return ",".join(parts)
    if isinstance(tags, (list, tuple)):
        parts = []
        for x in tags:
            if x is None:
                continue
            parts.append(str(x).strip())
        parts = [p for p in parts if p]
        return ",".join(parts)
    return str(tags).strip()


def has_jieba_for_tags() -> bool:
    """Return True if jieba is available for tag scoring heuristics."""
    return _JIEBA_FOR_TAGS_AVAILABLE


def tag_exact_match_bonus(query_keywords: List[str], tags_csv: str) -> float:
    """Return a small bonus if any keyword is an exact tag.

    Deprecated in favor of tag_match_score but kept for backward
    compatibility. It only checks whether *any* keyword exactly matches a
    tag (case-insensitive) and returns 1.0/0.0.
    """
    if not query_keywords or not tags_csv:
        return 0.0
    tag_set = {t.strip().lower() for t in tags_csv.replace("，", ",").split(",") if t.strip()}
    for kw in query_keywords:
        if kw.strip().lower() in tag_set:
            return 1.0
    return 0.0


def tag_match_score(query_keywords: List[str], tags_csv: str, *, max_tags_used: int = 10) -> float:
    """Score how well query keywords match the tag list (0..1).

    - Tags are stored as a comma-separated string; here we treat them as an
      ordered list of importance (t0, t1, ...).
    - For each query keyword, we look for the *first* exact tag match
      (case-insensitive) and add a contribution of 1/(1+rank), where rank
      is the tag index (0-based).
    - The final score is normalized by the best possible score for the
      given number of query keywords and tags, so it is always in [0, 1].
    """
    if not query_keywords or not tags_csv:
        return 0.0

    tags = [
        t.strip().lower()
        for t in tags_csv.replace("，", ",").split(",")
        if t.strip()
    ]
    if not tags:
        return 0.0

    # Limit the number of tags participating in the score so that very
    # long tag lists don't dominate.
    tags = tags[: max_tags_used or 10]
    index = {}
    for i, t in enumerate(tags):
        if t not in index:
            index[t] = i

    # Count contributions.
    raw_score = 0.0
    used_keywords = 0
    for kw in query_keywords:
        k = kw.strip().lower()
        if not k:
            continue
        used_keywords += 1
        if k in index:
            rank = index[k]
            raw_score += 1.0 / (1.0 + rank)

    if raw_score <= 0.0 or used_keywords == 0:
        return 0.0

    # Compute theoretical best score for normalization: all keywords match
    # the top tags.
    max_matches = min(used_keywords, len(tags))
    best = 0.0
    for i in range(max_matches):
        best += 1.0 / (1.0 + i)
    if best <= 0.0:
        return 0.0

    return min(raw_score / best, 1.0)


def extract_keywords_light(query: str, max_k: int = 10) -> List[str]:
    """Lightweight keyword extraction: split by whitespace and punctuation; keep meaningful tokens."""
    if not query:
        return []
    q = query.strip()
    # Replace punctuation with space
    q = re.sub(r"[^\w\u4e00-\u9fff]+", " ", q)
    parts = [p.strip() for p in q.split() if p.strip()]
    # Deduplicate preserving order
    seen = set()
    out = []
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= max_k:
            break
    return out


def build_fts_query_from_keywords(keywords: List[str]) -> str:
    """
    Build an FTS5 MATCH query from keywords.

    We use OR to broaden recall.
    Escape double quotes; wrap terms that contain special chars.
    """
    terms = []
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        kw = kw.replace('"', '""')
        # Quote if contains spaces
        if " " in kw:
            terms.append(f'"{kw}"')
        else:
            terms.append(kw)
    if not terms:
        return ""
    return " OR ".join(terms)
