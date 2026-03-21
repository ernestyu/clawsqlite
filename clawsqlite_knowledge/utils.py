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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

def now_iso_z() -> str:
    """Return current UTC time in ISO 8601 with Z suffix."""
    return _dt.datetime.utcnow().replace(tzinfo=_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_project_env(path: Optional[Path] = None) -> None:
    """Load project-level .env file into os.environ (if it exists).

    This is a lightweight replacement for python-dotenv, kept intentionally
    simple: KEY=VALUE per line, '#' starts a comment, blank lines ignored.
    We only set variables that are not already present in os.environ.
    """

    if path is None:
        path = Path.cwd() / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except Exception:
        return

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Strip optional surrounding single/double quotes: VAR="value" or VAR='value'
        if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def resolve_root_paths(
    cli_root: Optional[str] = None,
    cli_db: Optional[str] = None,
    cli_articles_dir: Optional[str] = None,
    default_root: Optional[str] = None,
) -> Dict[str, str]:
    """Resolve root/db/articles-dir with a clear priority chain.

    Priority for root:
    1. CLI --root
    2. CLAWSQLITE_ROOT env (preferred)
    3. legacy CLAWKB_ROOT env (fallback)
    4. default_root (if provided)
    5. $CLAWSQLITE_ROOT_FALLBACK or $CLAWKB_ROOT_FALLBACK or <cwd>/knowledge_data

    DB and articles dir follow the same pattern, but can be overridden via
    CLAWSQLITE_DB / CLAWSQLITE_ARTICLES_DIR (preferred) or legacy
    CLAWKB_DB / CLAWKB_ARTICLES_DIR.
    """

    # Root
    env_root_new = os.environ.get("CLAWSQLITE_ROOT")
    env_root_legacy = os.environ.get("CLAWKB_ROOT")
    root: Path
    if cli_root:
        root = Path(cli_root)
    elif env_root_new:
        root = Path(env_root_new)
    elif env_root_legacy:
        root = Path(env_root_legacy)
    elif default_root:
        root = Path(default_root)
    else:
        fallback_new = os.environ.get("CLAWSQLITE_ROOT_FALLBACK")
        fallback_legacy = os.environ.get("CLAWKB_ROOT_FALLBACK")
        if fallback_new:
            root = Path(fallback_new)
        elif fallback_legacy:
            root = Path(fallback_legacy)
        else:
            root = Path.cwd() / "knowledge_data"

    # DB
    env_db_new = os.environ.get("CLAWSQLITE_DB")
    env_db_legacy = os.environ.get("CLAWKB_DB")
    if cli_db:
        db_path = Path(cli_db)
    elif env_db_new:
        db_path = Path(env_db_new)
    elif env_db_legacy:
        db_path = Path(env_db_legacy)
    else:
        db_path = root / "knowledge.sqlite3"

    # Articles dir
    env_articles_new = os.environ.get("CLAWSQLITE_ARTICLES_DIR")
    env_articles_legacy = os.environ.get("CLAWKB_ARTICLES_DIR")
    if cli_articles_dir:
        articles_dir = Path(cli_articles_dir)
    elif env_articles_new:
        articles_dir = Path(env_articles_new)
    elif env_articles_legacy:
        articles_dir = Path(env_articles_legacy)
    else:
        articles_dir = root / "articles"

    return {
        "root": str(root),
        "db": str(db_path),
        "articles_dir": str(articles_dir),
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

def has_cjk(text: str, threshold: int = 2) -> bool:
    """Heuristic CJK detection: True if at least `threshold` CJK chars exist."""
    if not text:
        return False
    return len(_CJK_RE.findall(text)) >= threshold

def slugify(title: str, max_len: int = 60) -> str:
    """Generate a filesystem-safe slug (Unicode-friendly).

    Rules:
    - Normalize to NFKC
    - Preserve spaces (whitespace -> single space)
    - Letters (L*) and numbers (N*) are kept as-is
    - Any other non-space character becomes '-'
    - Collapse multiple '-' and trim leading/trailing '-'
    """

    s = (title or "").strip()
    if not s:
        return "untitled"

    # Normalize full-width / compatibility forms
    s = _ud.normalize("NFKC", s)

    out_chars: List[str] = []
    for ch in s:
        # Normalize any whitespace to a single space so English titles
        # remain readable ("Deep Learning 101"), while Chinese titles are
        # unaffected.
        if ch.isspace():
            out_chars.append(" ")
            continue
        cat = _ud.category(ch)
        if cat[0] in ("L", "N"):
            # Letter or Number (keep Unicode, including CJK)
            out_chars.append(ch)
        elif ch == "-":
            out_chars.append("-")
        else:
            # Replace other non-alnum characters with '-'
            out_chars.append("-")

    slug = "".join(out_chars)
    # Collapse multiple spaces and multiple '-' and trim
    slug = re.sub(r"\s{2,}", " ", slug)
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
