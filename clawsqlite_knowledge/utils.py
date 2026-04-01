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
    2. CLAWSQLITE_ROOT env
    3. default_root (if provided)
    4. <cwd>/knowledge_data

    DB and articles dir follow the same pattern, but can be overridden via
    CLAWSQLITE_DB / CLAWSQLITE_ARTICLES_DIR.
    """

    # Root
    env_root_new = os.environ.get("CLAWSQLITE_ROOT")
    root: Path
    if cli_root:
        root = Path(cli_root)
    elif env_root_new:
        root = Path(env_root_new)
    elif default_root:
        root = Path(default_root)
    else:
        root = Path.cwd() / "knowledge_data"

    # DB
    env_db_new = os.environ.get("CLAWSQLITE_DB")
    if cli_db:
        db_path = Path(cli_db)
    elif env_db_new:
        db_path = Path(env_db_new)
    else:
        db_path = root / "knowledge.sqlite3"

    # Articles dir
    env_articles_new = os.environ.get("CLAWSQLITE_ARTICLES_DIR")
    if cli_articles_dir:
        articles_dir = Path(cli_articles_dir)
    elif env_articles_new:
        articles_dir = Path(env_articles_new)
    else:
        articles_dir = root / "articles"

    return {
        "root": str(root),
        "db": str(db_path),
        "articles_dir": str(articles_dir),
    }


def resolve_interest_params(
    *,
    cli_min_size: Optional[int] = None,
    cli_max_clusters: Optional[int] = None,
    cli_alpha: Optional[float] = None,
) -> Dict[str, Any]:
    """Resolve interest clustering parameters with a clear priority chain.

    Priority for each parameter:
    1. CLI explicit value (if not None)
    2. Environment variable (CLAWSQLITE_INTEREST_*)
    3. Hard-coded default:
       - min_size: 5
       - max_clusters: 16
       - alpha: 0.4

    This helper keeps the policy in one place so CLI commands and
    internal tools can share the same behavior.
    """

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
            # Letter or Number (keep Unicode, including CJK)
            buf.append(ch)
        else:
            # punctuation / others -> boundary
            flush_buf()

    flush_buf()

    ascii_parts: List[str] = []
    for token in pieces:
        # If token contains CJK and pypinyin is available, use pinyin
        if has_cjk(token) and lazy_pinyin is not None:
            pinyin_list = lazy_pinyin(token)
            part = "-".join(pinyin_list)
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
        return ",".join([p for p in parts if p])
    return str(tags)
