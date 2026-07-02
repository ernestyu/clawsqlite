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

from clawsqlite_cli import main as top_cli_main
from clawsqlite_plumbing import db_cli, embed_cli, fs_cli, index_cli
from clawsqlite_knowledge import embed as embedmod
from tests.helpers import write_knowledge_config


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


def _run_catching_system_exit(func, argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = func(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 2
    return int(code), stdout.getvalue(), stderr.getvalue()


def _vec_ext_path() -> Path | None:
    candidates = []
    env_path = os.environ.get("CLAWSQLITE_VEC_EXT")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path("/app/node_modules/sqlite-vec-linux-x64/vec0.so"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@contextlib.contextmanager
def _cwd(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


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

    def _make_generated_title_articles_db(self, db_path: Path) -> None:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "CREATE TABLE articles(id INTEGER PRIMARY KEY, source_title TEXT, generated_title TEXT, tags TEXT, summary TEXT)"
            )
            conn.execute(
                "INSERT INTO articles(id, source_title, generated_title, tags, summary) "
                "VALUES(1, 'Source Alpha', 'Generated Alpha', 'tag1', 'hello alpha')"
            )
            conn.execute("CREATE VIRTUAL TABLE articles_fts USING fts5(title, tags, summary, body)")
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

    def test_admin_index_attempts_to_load_configured_vec_extension(self):
        class FakeConnection:
            def __init__(self) -> None:
                self.enabled = False
                self.loaded: list[str] = []

            def enable_load_extension(self, enabled: bool) -> None:
                self.enabled = enabled

            def load_extension(self, path: str) -> None:
                self.loaded.append(path)

        fake = FakeConnection()
        os.environ["CLAWSQLITE_TOKENIZER_EXT"] = "none"
        os.environ["CLAWSQLITE_VEC_EXT"] = "/tmp/example-vec0.so"

        index_cli._enable_extensions(fake)  # type: ignore[arg-type]

        self.assertTrue(fake.enabled)
        self.assertEqual(fake.loaded, ["/tmp/example-vec0.so"])

    def test_top_level_admin_index_check_reads_real_vec_table_when_extension_exists(self):
        vec_ext = _vec_ext_path()
        if vec_ext is None:
            self.skipTest("sqlite-vec extension is not available in this environment")

        with _tempdir() as tmpdir:
            root = tmpdir / "component"
            write_knowledge_config(root)
            db_path = root / "knowledge.sqlite3"
            os.environ["CLAWSQLITE_VEC_EXT"] = str(vec_ext)
            os.environ["CLAWSQLITE_TOKENIZER_EXT"] = "none"
            os.environ["CLAWSQLITE_VEC_DIM"] = "4"

            conn = sqlite3.connect(db_path)
            try:
                conn.enable_load_extension(True)
                try:
                    conn.load_extension(str(vec_ext))
                except Exception as e:
                    self.skipTest(f"sqlite-vec extension exists but cannot be loaded here: {e}")
                conn.execute("CREATE TABLE articles(id INTEGER PRIMARY KEY, title TEXT, tags TEXT, summary TEXT)")
                conn.execute("INSERT INTO articles(id, title, tags, summary) VALUES(1, 'Alpha', 'tag1', 'hello alpha')")
                conn.execute("CREATE VIRTUAL TABLE articles_fts USING fts5(title, tags, summary, body)")
                conn.execute("INSERT INTO articles_fts(rowid, title, tags, summary, body) VALUES(1, 'Alpha', 'tag1', 'hello alpha', '')")
                conn.execute("CREATE VIRTUAL TABLE articles_vec USING vec0(id INTEGER PRIMARY KEY, embedding float[4])")
                conn.execute(
                    "INSERT INTO articles_vec(id, embedding) VALUES(?, ?)",
                    (1, embedmod.floats_to_f32_blob([1.0, 0.0, 0.0, 0.0], dim=4)),
                )
                conn.commit()
            finally:
                conn.close()

            with _cwd(root):
                code, out, err = _run_catching_system_exit(
                    top_cli_main,
                    ["admin", "index", "check", "--table", "articles", "--fts-table", "articles_fts", "--vec-table", "articles_vec"],
                )

        self.assertEqual(code, 0, err)
        self.assertIn("[OK] FTS index articles_fts", out)
        self.assertIn("[OK] Vec index articles_vec matches base table articles (1 rows)", out)
        self.assertNotIn("no such module: vec0", out)

    def test_admin_index_rebuild_defaults_to_db_backed_fts_columns(self):
        with _tempdir() as tmpdir:
            db_path = tmpdir / "fts.sqlite3"
            self._make_articles_db(db_path)

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

    def test_admin_index_rebuild_maps_generated_title_to_fts_title(self):
        with _tempdir() as tmpdir:
            db_path = tmpdir / "fts_generated_title.sqlite3"
            self._make_generated_title_articles_db(db_path)

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
                ],
            )
            conn = sqlite3.connect(db_path)
            try:
                generated_count = conn.execute(
                    "SELECT count(*) FROM articles_fts WHERE articles_fts MATCH 'Generated'"
                ).fetchone()[0]
                source_count = conn.execute(
                    "SELECT count(*) FROM articles_fts WHERE articles_fts MATCH 'Source'"
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(code, 0, err)
        self.assertIn("[OK] Rebuilt FTS index articles_fts", out)
        self.assertEqual(generated_count, 1)
        self.assertEqual(source_count, 0)

    def test_admin_index_rebuild_rejects_explicit_non_db_backed_fts_columns(self):
        with _tempdir() as tmpdir:
            db_path = tmpdir / "fts.sqlite3"
            self._make_articles_db(db_path)

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
                        "--fts-cols",
                        "title,tags,summary,body",
                    ]
                )

        msg = str(cm.exception)
        self.assertIn("requested FTS columns are not present in the base table: body", msg)
        self.assertIn("DB-backed columns only", msg)
        self.assertNotIn("knowledge DB body text", msg)

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
        self.assertEqual(
            payload["items"],
            [
                {"kind": "fs_only", "class": "regular", "path": "orphan.md"},
                {"kind": "db_only", "class": "regular", "path": "missing.md"},
            ],
        )

    def test_admin_fs_gc_json_reports_actions_and_summary(self):
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
                    "--json",
                ],
            )
            payload = json.loads(out)
            conn = sqlite3.connect(db_path)
            try:
                rows = [r[0] for r in conn.execute("SELECT local_file_path FROM articles ORDER BY local_file_path")]
            finally:
                conn.close()

        self.assertEqual(code, 0, err)
        self.assertFalse((root / "orphan.md").exists())
        self.assertEqual(rows, ["live.md"])
        self.assertEqual(payload["deleted_fs"], ["orphan.md"])
        self.assertEqual(payload["deleted_db"], [{"rowid": 2, "path": "missing.md"}])
        self.assertEqual(payload["summary"]["deleted_fs_count"], 1)
        self.assertEqual(payload["summary"]["deleted_db_count"], 1)

    def test_top_level_admin_fs_repair_reconstructs_missing_file_from_summary(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "component"
            write_knowledge_config(root)
            db_path = root / "knowledge.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE articles("
                    "id INTEGER PRIMARY KEY, title TEXT, source_url TEXT, summary TEXT, "
                    "created_at TEXT, category TEXT, tags TEXT, priority INTEGER, local_file_path TEXT)"
                )
                conn.execute(
                    "INSERT INTO articles(id, title, source_url, summary, created_at, category, tags, priority, local_file_path) "
                    "VALUES(1, 'Missing Note', 'Local', 'summary body', '2026-01-01T00:00:00Z', 'note', 'demo', 0, '000001__missing-note.md')"
                )
                conn.commit()
            finally:
                conn.close()

            with _cwd(root):
                code, out, err = _run_catching_system_exit(
                    top_cli_main,
                    ["admin", "fs", "repair", "--no-scrape", "--json"],
                )
            payload = json.loads(out)
            repaired_path = root / "articles" / "000001__missing-note.md"

            self.assertEqual(code, 0, err)
            self.assertTrue(repaired_path.exists())
            content = repaired_path.read_text(encoding="utf-8")
            self.assertIn("summary body", content)
            self.assertEqual(payload["summary"]["repaired_count"], 1)
            self.assertEqual(payload["repaired"][0]["mode"], "summary")

    def test_top_level_admin_fs_repair_uses_configured_scraper_for_url_records(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "component"
            config_path = write_knowledge_config(root)
            scraper = tmpdir / "scrape.sh"
            scraper.write_text(
                "#!/bin/sh\n"
                "echo 'Title: Scraped Title'\n"
                "echo 'scraped markdown body'\n",
                encoding="utf-8",
            )
            scraper.chmod(0o755)
            with config_path.open("a", encoding="utf-8") as f:
                f.write(f'\n[scraper]\ncmd = "{scraper}"\n')

            db_path = root / "knowledge.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE articles("
                    "id INTEGER PRIMARY KEY, title TEXT, source_url TEXT, summary TEXT, "
                    "created_at TEXT, category TEXT, tags TEXT, priority INTEGER, local_file_path TEXT)"
                )
                conn.execute(
                    "INSERT INTO articles(id, title, source_url, summary, created_at, category, tags, priority, local_file_path) "
                    "VALUES(1, 'Original Title', 'https://example.test/a', 'old summary', '2026-01-01T00:00:00Z', 'web_article', 'demo', 0, '000001__article.md')"
                )
                conn.commit()
            finally:
                conn.close()

            with _cwd(root):
                code, out, err = _run_catching_system_exit(
                    top_cli_main,
                    ["admin", "fs", "repair", "--json"],
                )
            payload = json.loads(out)
            repaired_path = root / "articles" / "000001__article.md"

            self.assertEqual(code, 0, err)
            self.assertTrue(repaired_path.exists())
            content = repaired_path.read_text(encoding="utf-8")
            self.assertIn("Scraped Title", content)
            self.assertIn("scraped markdown body", content)
            self.assertEqual(payload["repaired"][0]["mode"], "scrape")

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

    def test_top_level_admin_requires_component_config_before_paths(self):
        with _tempdir() as tmpdir:
            with _cwd(tmpdir):
                code, out, err = _run_catching_system_exit(top_cli_main, ["admin", "db", "schema"])

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("ERROR_KIND: config_required", err)
        self.assertIn("clawsqlite.toml", err)

    def test_top_level_admin_reads_config_db_by_default(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "component"
            write_knowledge_config(root)
            db_path = root / "knowledge.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE t(x INTEGER)")
                conn.execute("INSERT INTO t(x) VALUES(1)")
                conn.commit()
            finally:
                conn.close()

            with _cwd(root):
                code, out, err = _run_catching_system_exit(
                    top_cli_main,
                    ["admin", "db", "exec", "--sql", "SELECT COUNT(*) AS n FROM t", "--json"],
                )

        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [{"n": 1}])

    def test_top_level_admin_explicit_db_overrides_config_db(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "component"
            write_knowledge_config(root)
            configured_db = root / "knowledge.sqlite3"
            override_db = tmpdir / "override.sqlite3"

            for db_path, values in ((configured_db, [1]), (override_db, [1, 2])):
                conn = sqlite3.connect(db_path)
                try:
                    conn.execute("CREATE TABLE t(x INTEGER)")
                    conn.executemany("INSERT INTO t(x) VALUES(?)", [(x,) for x in values])
                    conn.commit()
                finally:
                    conn.close()

            with _cwd(root):
                code, out, err = _run_catching_system_exit(
                    top_cli_main,
                    [
                        "admin",
                        "db",
                        "exec",
                        "--db",
                        str(override_db),
                        "--sql",
                        "SELECT COUNT(*) AS n FROM t",
                        "--json",
                    ],
                )

        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), [{"n": 2}])

    def test_top_level_admin_index_uses_config_defaults(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "component"
            write_knowledge_config(root)
            db_path = root / "knowledge.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE articles(id INTEGER PRIMARY KEY, title TEXT, tags TEXT, summary TEXT)")
                conn.execute("INSERT INTO articles(title, tags, summary) VALUES('Alpha', 'tag1', 'hello alpha')")
                conn.execute("CREATE VIRTUAL TABLE articles_fts USING fts5(title, tags, summary, body)")
                conn.execute("INSERT INTO articles_fts(rowid, title, tags, summary, body) VALUES(1, 'Alpha', 'tag1', 'hello alpha', '')")
                conn.commit()
            finally:
                conn.close()

            with _cwd(root):
                code, out, err = _run_catching_system_exit(top_cli_main, ["admin", "index", "check"])

        self.assertEqual(code, 0, err)
        self.assertIn("[OK] FTS index articles_fts", out)
        self.assertIn("Vec index articles_vec could not be checked", out)

    def test_top_level_admin_fs_uses_config_defaults(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "component"
            write_knowledge_config(root)
            articles_dir = root / "articles"
            articles_dir.mkdir(parents=True, exist_ok=True)
            (articles_dir / "live.md").write_text("live", encoding="utf-8")
            db_path = root / "knowledge.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE articles(id INTEGER PRIMARY KEY, local_file_path TEXT)")
                conn.execute("INSERT INTO articles(local_file_path) VALUES('live.md')")
                conn.commit()
            finally:
                conn.close()

            with _cwd(root):
                code, out, err = _run_catching_system_exit(
                    top_cli_main,
                    ["admin", "fs", "list-orphans", "--json"],
                )
            payload = json.loads(out)

        self.assertEqual(code, 0, err)
        self.assertEqual(payload["summary"]["fs_only"], 0)
        self.assertEqual(payload["summary"]["db_only"], 0)

    def test_top_level_admin_help_does_not_require_config(self):
        with _tempdir() as tmpdir:
            with _cwd(tmpdir):
                code, out, err = _run_catching_system_exit(top_cli_main, ["admin", "--help"])

        self.assertEqual(code, 0, err)
        self.assertIn("same clawsqlite.toml", out)
        self.assertIn("db", out)

    def test_top_level_admin_embed_uses_config_runtime_defaults(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "component"
            write_knowledge_config(root)
            db_path = root / "knowledge.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE articles(id INTEGER PRIMARY KEY, summary TEXT, deleted_at TEXT)")
                conn.execute("CREATE TABLE articles_vec(id INTEGER PRIMARY KEY, embedding BLOB)")
                conn.execute("INSERT INTO articles(summary, deleted_at) VALUES('hello summary', NULL)")
                conn.commit()
            finally:
                conn.close()

            def fake_get_embedding(text: str, *, timeout: int = 300):
                self.assertEqual(os.environ.get("EMBEDDING_MODEL"), "test-embedding")
                self.assertEqual(os.environ.get("EMBEDDING_API_KEY"), "test-embedding-key")
                self.assertEqual(os.environ.get("CLAWSQLITE_VEC_DIM"), "4")
                return [1.0, 0.0, 0.0, 0.0]

            embedmod.get_embedding = fake_get_embedding

            with _cwd(root):
                code, out, err = _run_catching_system_exit(
                    top_cli_main,
                    ["admin", "embed", "column", "--limit", "1"],
                )

            conn = sqlite3.connect(db_path)
            try:
                rows = list(conn.execute("SELECT id, length(embedding) FROM articles_vec"))
            finally:
                conn.close()

        self.assertEqual(code, 0, err)
        self.assertIn("[OK] embed-column", out)
        self.assertEqual(rows, [(1, 16)])

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
