# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from clawsqlite_knowledge import db as dbmod


class TitleSchemaMigrationTests(unittest.TestCase):
    def test_legacy_title_column_migrates_to_source_and_generated_titles(self):
        with tempfile.TemporaryDirectory(prefix="clawsqlite_title_schema_") as tmp:
            db_path = Path(tmp) / "knowledge.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    """
                    CREATE TABLE articles (
                      id INTEGER PRIMARY KEY,
                      title TEXT,
                      source_url TEXT,
                      tags TEXT,
                      summary TEXT,
                      created_at TEXT NOT NULL,
                      modified_at TEXT NOT NULL,
                      deleted_at TEXT,
                      category TEXT,
                      local_file_path TEXT,
                      priority INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO articles(id, title, source_url, tags, summary, created_at, modified_at, category, priority)
                    VALUES(1, 'Legacy Title', 'Local', 'tag1', 'summary', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'note', 0)
                    """
                )
                conn.commit()
            finally:
                conn.close()

            conn = dbmod.open_db(str(db_path), need_fts=True, need_vec=False, tokenizer_ext="none")
            try:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(articles)")}
                row = conn.execute(
                    "SELECT source_title, generated_title, summary FROM articles WHERE id=1"
                ).fetchone()
            finally:
                conn.close()

        self.assertIn("source_title", cols)
        self.assertIn("generated_title", cols)
        self.assertNotIn("title", cols)
        self.assertEqual(row["source_title"], "Legacy Title")
        self.assertEqual(row["generated_title"], "Legacy Title")
        self.assertEqual(row["summary"], "summary")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
