# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from clawsqlite_plumbing import db_cli, embed_cli, fs_cli, index_cli
from clawsqlite_knowledge import embed as embedmod


@contextlib.contextmanager
def _tempdir():
    path = Path(tempfile.mkdtemp(prefix="clawsqlite_admin_"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _run(func, argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = func(argv)
    return int(code), stdout.getvalue(), stderr.getvalue()


class AdminCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()
        self._get_embedding = embedmod.get_embedding

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        embedmod.get_embedding = self._get_embedding

    def _make_articles_db(self, db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE articles(id INTEGER PRIMARY KEY, title TEXT, tags TEXT, summary TEXT)")
            conn.execute("INSERT INTO articles(title, tags, summary) VALUES('Alpha', 'tag1', 'hello alpha')")
            conn.execute("CREATE VIRTUAL TABLE articles_fts USING fts5(title, tags, summary, body)")
            conn.execute("INSERT INTO articles_fts(rowid, title, tags, summary) VALUES(1, 'Alpha', 'tag1', 'hello alpha')")
            conn.commit()
        finally:
            conn.close()

    def test_admin_index_check_warns_when_vec0_is_unavailable(self):
        with _tempdir() as tmpdir:
            db_path = tmpdir / "vec_missing.sqlite3"
            self._make_articles_db(db_path)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("PRAGMA writable_schema=ON")
                conn.execute(
                    "INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql) "
                    "VALUES('table','articles_vec','articles_vec',0,"
                    "'CREATE VIRTUAL TABLE articles_vec USING vec0(id INTEGER PRIMARY KEY, embedding float[4])')"
                )
                conn.commit()
            finally:
                conn.close()

            code, out, err = _run(
                index_cli.main,
                [
                    "check",
                    "--db",
                    str(db_path),
                    "--table",
                    "articles",
                    "--fts-table",
                    "articles_fts",
                    "--vec-table",
                    "articles_vec",
                ],
            )

        self.assertEqual(code, 0, err)
        self.assertIn("[OK] FTS index articles_fts", out)
        self.assertIn("[WARN] Vec index articles_vec could not be checked", out)
        self.assertIn("NEXT: load sqlite-vec", out)

    def test_admin_index_rebuild_requires_matching_columns_or_explicit_fts_cols(self):
        with _tempdir() as tmpdir:
            db_path = tmpdir / "fts.sqlite3"
            self._make_articles_db(db_path)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                with self.assertRaises(SystemExit) as cm:
                    index_cli.main(
                        [
                            "rebuild",
                            "--db",
                            str(db_path),
                            "--table",
                            "articles",
                            "--fts-table",
                            "articles_fts",
                        ]
                    )
            self.assertIn("columns are missing: body", str(cm.exception))
            self.assertIn("clawsqlite knowledge reindex --rebuild --fts", str(cm.exception))

            code, out, err = _run(
                index_cli.main,
                [
                    "rebuild",
                    "--db",
                    str(db_path),
                    "--table",
                    "articles",
                    "--fts-table",
                    "articles_fts",
                    "--fts-cols",
                    "title,tags,summary",
                ],
            )
            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute("SELECT count(*) FROM articles_fts WHERE articles_fts MATCH 'hello'").fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(code, 0, err)
        self.assertIn("[OK] Rebuilt FTS index articles_fts", out)
        self.assertEqual(count, 1)

    def test_admin_fs_gc_dry_run_and_real_run_handle_rowid(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "files"
            root.mkdir()
            (root / "live.md").write_text("live", encoding="utf-8")
            (root / "orphan.md").write_text("orphan", encoding="utf-8")
            db_path = tmpdir / "fs.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE articles(local_file_path TEXT)")
                conn.execute("INSERT INTO articles(local_file_path) VALUES('live.md')")
                conn.execute("INSERT INTO articles(local_file_path) VALUES('missing.md')")
                conn.commit()
            finally:
                conn.close()

            dry_code, dry_out, dry_err = _run(
                fs_cli.main,
                [
                    "gc",
                    "--root",
                    str(root),
                    "--db",
                    str(db_path),
                    "--table",
                    "articles",
                    "--path-col",
                    "local_file_path",
                    "--delete-fs-orphans",
                    "--delete-db-orphans",
                    "--dry-run",
                ],
            )
            self.assertEqual(dry_code, 0, dry_err)
            self.assertIn("[DRY_RUN][DELETE_FS] orphan.md", dry_out)
            self.assertIn("[DRY_RUN][DELETE_DB]", dry_out)
            self.assertTrue((root / "orphan.md").exists())

            code, out, err = _run(
                fs_cli.main,
                [
                    "gc",
                    "--root",
                    str(root),
                    "--db",
                    str(db_path),
                    "--table",
                    "articles",
                    "--path-col",
                    "local_file_path",
                    "--delete-fs-orphans",
                    "--delete-db-orphans",
                ],
            )
            conn = sqlite3.connect(db_path)
            try:
                rows = [r[0] for r in conn.execute("SELECT local_file_path FROM articles ORDER BY local_file_path")]
            finally:
                conn.close()

        self.assertEqual(code, 0, err)
        self.assertIn("[DELETE_FS] orphan.md", out)
        self.assertIn("[DELETE_DB]", out)
        self.assertFalse((root / "orphan.md").exists())
        self.assertEqual(rows, ["live.md"])

    def test_admin_fs_list_orphans_json_includes_summary(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "files"
            root.mkdir()
            (root / "orphan.md").write_text("orphan", encoding="utf-8")
            db_path = tmpdir / "fs.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE articles(local_file_path TEXT)")
                conn.execute("INSERT INTO articles(local_file_path) VALUES('missing.md')")
                conn.commit()
            finally:
                conn.close()

            code, out, err = _run(
                fs_cli.main,
                [
                    "list-orphans",
                    "--root",
                    str(root),
                    "--db",
                    str(db_path),
                    "--table",
                    "articles",
                    "--path-col",
                    "local_file_path",
                    "--json",
                ],
            )
            payload = json.loads(out)

        self.assertEqual(code, 0, err)
        self.assertEqual(payload["summary"]["fs_only"], 1)
        self.assertEqual(payload["summary"]["db_only"], 1)
        self.assertEqual(payload["fs_only"][0]["path"], "orphan.md")
        self.assertEqual(payload["db_only"][0]["path"], "missing.md")

    def test_admin_db_exec_prints_select_results(self):
        with _tempdir() as tmpdir:
            db_path = tmpdir / "db.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE t(x INTEGER)")
                conn.execute("INSERT INTO t(x) VALUES(1)")
                conn.commit()
            finally:
                conn.close()

            code, out, err = _run(db_cli.main, ["exec", "--db", str(db_path), "--sql", "SELECT COUNT(*) AS n FROM t"])
            json_code, json_out, json_err = _run(
                db_cli.main,
                ["exec", "--db", str(db_path), "--sql", "SELECT COUNT(*) AS n FROM t", "--json"],
            )

        self.assertEqual(code, 0, err)
        self.assertIn("n\n1\n", out)
        self.assertEqual(json_code, 0, json_err)
        self.assertEqual(json.loads(json_out), [{"n": 1}])

    def test_admin_embed_column_wraps_embedding_errors(self):
        with _tempdir() as tmpdir:
            db_path = tmpdir / "embed.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE articles(id INTEGER PRIMARY KEY, summary TEXT)")
                conn.execute("CREATE TABLE articles_vec(id INTEGER PRIMARY KEY, embedding BLOB)")
                conn.execute("INSERT INTO articles(summary) VALUES('hello')")
                conn.commit()
            finally:
                conn.close()

            os.environ["EMBEDDING_BASE_URL"] = "https://embed.example.test/v1"
            os.environ["EMBEDDING_MODEL"] = "test-embedding"

            def fake_get_embedding(text: str, *, timeout: int = 300):
                raise RuntimeError("Embedding HTTPError: 502 Bad Gateway")

            embedmod.get_embedding = fake_get_embedding

            code, out, err = _run(
                embed_cli.main,
                [
                    "column",
                    "--db",
                    str(db_path),
                    "--table",
                    "articles",
                    "--id-col",
                    "id",
                    "--text-col",
                    "summary",
                    "--vec-table",
                    "articles_vec",
                ],
            )

        self.assertEqual(code, 4)
        self.assertEqual(out, "")
        self.assertIn("ERROR: embedding request failed for row id=1", err)
        self.assertIn("provider=https://embed.example.test/v1", err)
        self.assertIn("NEXT: check embedding service health", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
