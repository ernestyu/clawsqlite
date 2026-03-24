# -*- coding: utf-8 -*-
"""
SQLite layer for clawsqlite_knowledge (knowledge app).

Tables
- articles: source of truth.
- articles_fts: FTS5 (contentless) table kept in sync by the app.
- articles_vec: vec0 (sqlite-vec) table for vector search (optional).

Optional extensions
- libsimple.so (tokenizer "simple") for better Chinese tokenization.
- vec0.so for sqlite-vec.

Degradation
- If tokenizer is missing: create articles_fts without tokenize='simple'.
- If vec0 is missing or CLAWSQLITE_VEC_DIM is unset/invalid: skip creating
  articles_vec; vec features become unavailable.
"""
from __future__ import annotations

import os
import re
import sqlite3
import glob
from typing import Any, Dict, List, Optional, Tuple

from .utils import now_iso_z, has_cjk, extract_keywords_light

# Optional Chinese tokenizer: jieba
try:  # pragma: no cover - optional dependency
    import jieba  # type: ignore
    _JIEBA_AVAILABLE = True
except Exception:
    jieba = None  # type: ignore
    _JIEBA_AVAILABLE = False

# Defaults for clawsqlite_knowledge.
DEFAULT_DB_PATH = os.environ.get("CLAWSQLITE_DB_DEFAULT", "knowledge.sqlite3")
DEFAULT_TOKENIZER_EXT = os.environ.get("CLAWSQLITE_TOKENIZER_EXT_DEFAULT", "/usr/local/lib/libsimple.so")

_VEC_GLOBS = [
    "/app/node_modules/**/vec0.so",
    "/usr/local/lib/**/vec0.so",
    "/usr/lib/**/vec0.so",
    "/lib/**/vec0.so",
]

BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles (
  id               INTEGER PRIMARY KEY,
  title            TEXT,
  source_url       TEXT,
  tags             TEXT,
  summary          TEXT,
  created_at       TEXT NOT NULL,
  modified_at      TEXT NOT NULL,
  deleted_at       TEXT,
  category         TEXT,
  local_file_path  TEXT,
  priority         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_articles_created_at  ON articles(created_at);
CREATE INDEX IF NOT EXISTS idx_articles_modified_at ON articles(modified_at);
CREATE INDEX IF NOT EXISTS idx_articles_deleted_at  ON articles(deleted_at);
CREATE INDEX IF NOT EXISTS idx_articles_category    ON articles(category);
CREATE INDEX IF NOT EXISTS idx_articles_priority    ON articles(priority);
CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_source_url_unique
  ON articles(source_url)
  WHERE source_url IS NOT NULL AND trim(source_url) != '' AND source_url != 'Local';
"""

FTS_SCHEMA_SIMPLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
  title,
  tags,
  summary,
  tokenize='simple'
);
"""

FTS_SCHEMA_FALLBACK = """
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
  title,
  tags,
  summary
);
"""

_FTS_TOKENIZER_RE = re.compile(r"tokenize\s*=\s*['\"]simple['\"]", re.IGNORECASE)
_WORD_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]")

def _fts_uses_simple_tokenizer(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='articles_fts'"
        ).fetchone()
        if not row:
            return False
        sql = row[0] or ""
        return bool(_FTS_TOKENIZER_RE.search(sql))
    except Exception:
        return False

def fts_jieba_enabled(conn: sqlite3.Connection) -> bool:
    """Return True if we should use jieba-based tokenization for FTS text/query.

    Policy (env CLAWSQLITE_FTS_JIEBA):
    - off/0/false: disable
    - on/1/true: enable when jieba is available
    - auto (default): enable only when jieba is available AND FTS is using
      the fallback tokenizer (i.e. no 'simple' tokenizer loaded).
    """
    mode = (os.environ.get("CLAWSQLITE_FTS_JIEBA") or "auto").strip().lower()
    if mode in ("0", "false", "off", "no"):
        return False
    elif not _JIEBA_AVAILABLE:
        return False
    elif mode in ("1", "true", "on", "yes", "force"):
        return True
    else:
        # auto: only when FTS is not using the 'simple' tokenizer.
        return not _fts_uses_simple_tokenizer(conn)

def _jieba_tokens(text: str) -> List[str]:
    if not text:
        return []
    if not _JIEBA_AVAILABLE:
        return []
    try:
        return [t.strip() for t in jieba.cut(text) if t and t.strip()]
    except Exception:
        return []

def _fts_text_for_index(conn: sqlite3.Connection, text: Optional[str]) -> str:
    if not text:
        return ""
    if not fts_jieba_enabled(conn):
        return text
    if not has_cjk(text, threshold=2):
        return text
    tokens = _jieba_tokens(text)
    return " ".join(tokens) if tokens else text

def _jieba_keywords(text: str, *, max_k: int) -> List[str]:
    tokens = _jieba_tokens(text)
    if not tokens:
        return extract_keywords_light(text, max_k=max_k)
    out: List[str] = []
    seen = set()
    for t in tokens:
        if not _WORD_TOKEN_RE.search(t):
            continue
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(t)
        if len(out) >= max_k:
            break
    return out

def fts_keywords_for_query(conn: sqlite3.Connection, query: str, *, max_k: int = 10) -> List[str]:
    if not query:
        return []
    if not fts_jieba_enabled(conn):
        return extract_keywords_light(query, max_k=max_k)
    if not has_cjk(query, threshold=1):
        return extract_keywords_light(query, max_k=max_k)
    return _jieba_keywords(query, max_k=max_k)

def fts_normalize_keywords(conn: sqlite3.Connection, keywords: List[str], *, max_k: int = 10) -> List[str]:
    if not keywords:
        return []
    if not fts_jieba_enabled(conn):
        return keywords[:max_k]
    out: List[str] = []
    seen = set()
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        if has_cjk(kw, threshold=1):
            toks = _jieba_tokens(kw) or [kw]
        else:
            toks = [kw]
        for t in toks:
            t = t.strip()
            if not t:
                continue
            if not _WORD_TOKEN_RE.search(t):
                continue
            low = t.lower()
            if low in seen:
                continue
            seen.add(low)
            out.append(t)
            if len(out) >= max_k:
                return out
    return out

def fts_fallback_warning(conn: sqlite3.Connection) -> Optional[Dict[str, str]]:
    """Return a warning payload when FTS is in unicode61 fallback without jieba."""
    if _fts_uses_simple_tokenizer(conn):
        return None
    if fts_jieba_enabled(conn):
        return None
    # At this point: no simple tokenizer, and jieba fallback disabled/unavailable.
    mode = (os.environ.get("CLAWSQLITE_FTS_JIEBA") or "auto").strip().lower()
    if _JIEBA_AVAILABLE and mode in ("0", "false", "off", "no"):
        next_hint = (
            "set CLAWSQLITE_FTS_JIEBA=on to enable jieba-based fallback tokenization, "
            "or provide libsimple.so if you can install extensions."
        )
    else:
        next_hint = (
            "install jieba (pip install jieba) to enable CJK fallback tokenization; "
            "libsimple.so often requires root/extension install."
        )
    return {
        "message": "FTS is using the default unicode61 tokenizer; CJK recall will be poor.",
        "error_kind": "fts_tokenizer_fallback",
        "next": next_hint,
    }

def _vec_schema() -> Optional[str]:
    dim_env = os.environ.get("CLAWSQLITE_VEC_DIM")
    if not dim_env:
        return None
    try:
        dim = int(dim_env)
    except Exception:
        return None
    if dim <= 0:
        return None
    return f"""
CREATE VIRTUAL TABLE IF NOT EXISTS articles_vec USING vec0(
  id INTEGER PRIMARY KEY,
  embedding float[{dim}]
);
"""

def _find_vec0_so() -> Optional[str]:
    for pat in _VEC_GLOBS:
        hits = glob.glob(pat, recursive=True)
        if hits:
            hits.sort()
            return hits[0]
    return None

def _try_enable_ext(conn: sqlite3.Connection) -> None:
    try:
        conn.enable_load_extension(True)
    except Exception:
        pass

def _try_load_ext(conn: sqlite3.Connection, path: str) -> bool:
    try:
        conn.load_extension(path)
        return True
    except Exception:
        return False

def _fts_table_ok(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1 FROM articles_fts LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def vec_table_exists(conn: sqlite3.Connection) -> bool:
    """Return True if articles_vec exists and is readable.

    We use a lightweight SELECT to avoid relying on sqlite_master parsing
    in multiple places.
    """

    try:
        conn.execute("SELECT 1 FROM articles_vec LIMIT 1").fetchone()
        return True
    except Exception:
        return False

def _drop_fts(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("DROP TABLE IF EXISTS articles_fts")
    except Exception:
        pass

def open_db(
    db_path: str,
    *,
    need_fts: bool,
    need_vec: bool,
    tokenizer_ext: Optional[str] = None,
    vec_ext: Optional[str] = None,
) -> sqlite3.Connection:
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    _try_enable_ext(conn)

    conn.executescript(BASE_SCHEMA_SQL)

    if need_fts:
        ext = tokenizer_ext or os.environ.get("CLAWSQLITE_TOKENIZER_EXT") or DEFAULT_TOKENIZER_EXT
        tokenizer_loaded = False
        if ext and ext.lower() != "none":
            tokenizer_loaded = _try_load_ext(conn, ext)

        if tokenizer_loaded:
            try:
                conn.executescript(FTS_SCHEMA_SIMPLE)
            except Exception:
                conn.executescript(FTS_SCHEMA_FALLBACK)
        else:
            conn.executescript(FTS_SCHEMA_FALLBACK)

        if not _fts_table_ok(conn):
            _drop_fts(conn)
            conn.executescript(FTS_SCHEMA_FALLBACK)

    if need_vec:
        ext = vec_ext or os.environ.get("CLAWSQLITE_VEC_EXT") or _find_vec0_so()
        if ext and ext.lower() != "none":
            _try_load_ext(conn, ext)
        schema = _vec_schema()
        if schema:
            try:
                conn.executescript(schema)
            except Exception:
                pass

    conn.commit()
    return conn

def insert_article(
    conn: sqlite3.Connection,
    *,
    title: str,
    source_url: str,
    tags: str,
    summary: str,
    category: str,
    local_file_path: str,
    priority: int,
    created_at: Optional[str] = None,
) -> int:
    ts = created_at or now_iso_z()
    cur = conn.execute(
        """
        INSERT INTO articles(title, source_url, tags, summary, created_at, modified_at, deleted_at, category, local_file_path, priority)
        VALUES(?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
        """,
        (title, source_url, tags, summary, ts, ts, category, local_file_path, priority),
    )
    return int(cur.lastrowid)

def update_article_fields(
    conn: sqlite3.Connection,
    article_id: int,
    *,
    title: Optional[str] = None,
    tags: Optional[str] = None,
    summary: Optional[str] = None,
    category: Optional[str] = None,
    local_file_path: Optional[str] = None,
    priority: Optional[int] = None,
    deleted_at: Optional[str] = None,
) -> None:
    fields = []
    vals: List[Any] = []

    if title is not None:
        fields.append("title=?")
        vals.append(title)
    if tags is not None:
        fields.append("tags=?")
        vals.append(tags)
    if summary is not None:
        fields.append("summary=?")
        vals.append(summary)
    if category is not None:
        fields.append("category=?")
        vals.append(category)
    if local_file_path is not None:
        fields.append("local_file_path=?")
        vals.append(local_file_path)
    if priority is not None:
        fields.append("priority=?")
        vals.append(priority)
    if deleted_at is not None:
        fields.append("deleted_at=?")
        vals.append(deleted_at)

    fields.append("modified_at=?")
    vals.append(now_iso_z())

    vals.append(article_id)
    conn.execute(f"UPDATE articles SET {', '.join(fields)} WHERE id=?", vals)

def get_article(conn: sqlite3.Connection, article_id: int):
    return conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()

def delete_article_row(conn: sqlite3.Connection, article_id: int) -> None:
    conn.execute("DELETE FROM articles WHERE id=?", (article_id,))


def get_article_by_source(conn: sqlite3.Connection, source_url: str):
    """Return the first article row for a given source_url.

    We intentionally DO NOT filter deleted_at here so that callers like
    ingest --update-existing and delete --hard can still operate on
    soft-deleted rows (ghost records) and either refresh or hard-delete
    them. Search/normal flows control visibility via higher-level flags.
    """
    return conn.execute(
        "SELECT * FROM articles WHERE source_url=? ORDER BY id ASC LIMIT 1",
        (source_url,),
    ).fetchone()

def upsert_fts(conn: sqlite3.Connection, article_id: int, title: str, tags: str, summary: str) -> None:
    conn.execute("DELETE FROM articles_fts WHERE rowid=?", (article_id,))
    title = _fts_text_for_index(conn, title)
    tags = _fts_text_for_index(conn, tags)
    summary = _fts_text_for_index(conn, summary)
    conn.execute(
        "INSERT INTO articles_fts(rowid, title, tags, summary) VALUES(?, ?, ?, ?)",
        (article_id, title or "", tags or "", summary or ""),
    )

def delete_fts(conn: sqlite3.Connection, article_id: int) -> None:
    conn.execute("DELETE FROM articles_fts WHERE rowid=?", (article_id,))

def upsert_vec(conn: sqlite3.Connection, article_id: int, embedding_blob: bytes) -> None:
    conn.execute("INSERT OR REPLACE INTO articles_vec(id, embedding) VALUES(?, ?)", (article_id, embedding_blob))

def delete_vec(conn: sqlite3.Connection, article_id: int) -> None:
    conn.execute("DELETE FROM articles_vec WHERE id=?", (article_id,))

def rebuild_fts(conn: sqlite3.Connection, include_deleted: bool = False) -> None:
    conn.execute("DELETE FROM articles_fts")
    if fts_jieba_enabled(conn):
        if include_deleted:
            rows = conn.execute(
                "SELECT id, title, tags, summary FROM articles"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, tags, summary FROM articles WHERE deleted_at IS NULL"
            ).fetchall()
        for r in rows:
            aid = int(r["id"])
            title = _fts_text_for_index(conn, r["title"] or "")
            tags = _fts_text_for_index(conn, r["tags"] or "")
            summary = _fts_text_for_index(conn, r["summary"] or "")
            conn.execute(
                "INSERT INTO articles_fts(rowid, title, tags, summary) VALUES(?, ?, ?, ?)",
                (aid, title, tags, summary),
            )
    else:
        if include_deleted:
            conn.execute(
                "INSERT INTO articles_fts(rowid, title, tags, summary) "
                "SELECT id, coalesce(title,''), coalesce(tags,''), coalesce(summary,'') FROM articles"
            )
        else:
            conn.execute(
                "INSERT INTO articles_fts(rowid, title, tags, summary) "
                "SELECT id, coalesce(title,''), coalesce(tags,''), coalesce(summary,'') FROM articles WHERE deleted_at IS NULL"
            )

def vec_knn(conn: sqlite3.Connection, query_vec_blob: bytes, k: int, include_deleted: bool = False) -> List[Tuple[int, float]]:
    if include_deleted:
        rows = conn.execute(
            "SELECT id, distance FROM articles_vec WHERE embedding MATCH ? AND k = ?",
            (query_vec_blob, k),
        ).fetchall()
    else:
        sql = """
        SELECT v.id AS id, v.distance AS distance
        FROM articles_vec v
        JOIN articles a ON a.id = v.id
        WHERE a.deleted_at IS NULL
          AND v.embedding MATCH ? AND k = ?
        """
        rows = conn.execute(sql, (query_vec_blob, k)).fetchall()
    return [(int(r["id"]), float(r["distance"])) for r in rows]

def fts_search(conn: sqlite3.Connection, fts_query: str, limit: int, include_deleted: bool = False) -> List[Tuple[int, float]]:
    if include_deleted:
        sql = """
        SELECT rowid AS id, bm25(articles_fts) AS score
        FROM articles_fts
        WHERE articles_fts MATCH ?
        ORDER BY score
        LIMIT ?
        """
        rows = conn.execute(sql, (fts_query, limit)).fetchall()
    else:
        sql = """
        SELECT a.id AS id, bm25(articles_fts) AS score
        FROM articles_fts
        JOIN articles a ON a.id = articles_fts.rowid
        WHERE a.deleted_at IS NULL
          AND articles_fts MATCH ?
        ORDER BY score
        LIMIT ?
        """
        rows = conn.execute(sql, (fts_query, limit)).fetchall()
    return [(int(r["id"]), float(r["score"])) for r in rows]

def count_missing(conn: sqlite3.Connection) -> Dict[str, int]:
    def _count(where: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) AS c FROM articles WHERE deleted_at IS NULL AND ({where})").fetchone()["c"])
    return {
        "title": _count("title IS NULL OR trim(title) = ''"),
        "summary": _count("summary IS NULL OR trim(summary) = ''"),
        "tags": _count("tags IS NULL OR trim(tags) = ''"),
        "path": _count("local_file_path IS NULL OR trim(local_file_path) = ''"),
        "created_at": _count("created_at IS NULL OR trim(created_at) = ''"),
        "modified_at": _count("modified_at IS NULL OR trim(modified_at) = ''"),
    }

def count_file_missing(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT local_file_path FROM articles WHERE deleted_at IS NULL AND local_file_path IS NOT NULL AND trim(local_file_path) != ''"
    ).fetchall()
    missing = 0
    for r in rows:
        p = str(r["local_file_path"])
        if not os.path.exists(p):
            missing += 1
    return missing

def count_fts_missing(conn: sqlite3.Connection) -> int:
    r = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM articles a
        LEFT JOIN articles_fts f ON f.rowid = a.id
        WHERE a.deleted_at IS NULL AND f.rowid IS NULL
        """
    ).fetchone()
    return int(r["c"])

def count_vec_missing(conn: sqlite3.Connection) -> int:
    r = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM articles a
        LEFT JOIN articles_vec v ON v.id = a.id
        WHERE a.deleted_at IS NULL
          AND a.summary IS NOT NULL AND trim(a.summary) != ''
          AND v.id IS NULL
        """
    ).fetchone()
    return int(r["c"])
