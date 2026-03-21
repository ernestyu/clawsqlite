# -*- coding: utf-8 -*-
"""
SQLite layer for clawkb.

Tables
- articles: source of truth.
- articles_fts: FTS5 (contentless) table kept in sync by the app.
- articles_vec: vec0 (sqlite-vec) table for vector search (optional).

Optional extensions
- libsimple.so (tokenizer "simple") for better Chinese tokenization.
- vec0.so for sqlite-vec.

Degradation
- If tokenizer is missing: create articles_fts without tokenize='simple'.
- If vec0 is missing: skip creating articles_vec; vec features become unavailable.
"""
from __future__ import annotations

import os
import sqlite3
import glob
from typing import Any, Dict, List, Optional, Tuple

from .utils import now_iso_z

DEFAULT_DB_PATH = os.environ.get("CLAWKB_DB_DEFAULT", "/home/node/.openclaw/workspace/clawkb/clawkb.sqlite3")
DEFAULT_TOKENIZER_EXT = os.environ.get("CLAWKB_TOKENIZER_EXT_DEFAULT", "/usr/local/lib/libsimple.so")

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

def _vec_schema() -> str:
    dim_env = os.environ.get("CLAWKB_VEC_DIM")
    try:
        dim = int(dim_env) if dim_env else 1536
    except Exception:
        dim = 1536
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
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    _try_enable_ext(conn)

    conn.executescript(BASE_SCHEMA_SQL)

    if need_fts:
        ext = tokenizer_ext or os.environ.get("CLAWKB_TOKENIZER_EXT") or DEFAULT_TOKENIZER_EXT
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
        ext = vec_ext or os.environ.get("CLAWKB_VEC_EXT") or _find_vec0_so()
        if ext and ext.lower() != "none":
            _try_load_ext(conn, ext)
        try:
            conn.executescript(_vec_schema())
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
