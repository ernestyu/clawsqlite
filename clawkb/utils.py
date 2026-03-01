# -*- coding: utf-8 -*-
"""
Utilities for clawkb.

All code comments are in English by design.
"""
from __future__ import annotations

import os
import re
import json
import datetime as _dt
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

def now_iso_z() -> str:
    """Return current UTC time in ISO 8601 with Z suffix."""
    return _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc).isoformat().replace("+00:00", "Z")

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

def has_cjk(text: str, threshold: int = 2) -> bool:
    """Heuristic CJK detection: True if at least `threshold` CJK chars exist."""
    if not text:
        return False
    return len(_CJK_RE.findall(text)) >= threshold

_SLUG_KEEP_RE = re.compile(r"[^a-z0-9\-]+")

def slugify(title: str, max_len: int = 60) -> str:
    """Generate a filesystem-safe slug."""
    s = (title or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = _SLUG_KEEP_RE.sub("", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "untitled"
    return s[:max_len]

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

def tag_exact_match_bonus(query_keywords: List[str], tags_csv: str) -> float:
    """Return a small bonus if any keyword is an exact tag."""
    if not query_keywords or not tags_csv:
        return 0.0
    tag_set = {t.strip().lower() for t in tags_csv.replace("，", ",").split(",") if t.strip()}
    for kw in query_keywords:
        if kw.strip().lower() in tag_set:
            return 1.0
    return 0.0

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
