# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tarfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from clawsqlite_knowledge import cli as knowledge_cli
from tests.helpers import write_knowledge_config


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_TMP = Path(os.environ.get("CLAWSQLITE_TEST_TMP", str(REPO_ROOT / ".tmp_tests")))
BASE_TMP.mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def _tempdir():
    path = BASE_TMP / f"tmp_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@contextlib.contextmanager
def _cwd(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class KnowledgeBackupTests(unittest.TestCase):
    def test_backup_uploads_configured_s3_archive_with_db_and_articles(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            write_knowledge_config(root)
            (root / "knowledge.sqlite3").write_bytes(b"sqlite-data")
            articles = root / "articles"
            articles.mkdir(parents=True, exist_ok=True)
            (articles / "000001__hello.md").write_text("# Hello\n", encoding="utf-8")

            captured = {}

            def fake_upload(archive_path, s3, object_key):
                captured["bucket"] = s3.bucket
                captured["prefix"] = s3.prefix
                captured["endpoint_url"] = s3.endpoint_url
                captured["region"] = s3.region
                captured["access_key_id"] = s3.access_key_id
                captured["secret_access_key"] = s3.secret_access_key
                captured["object_key"] = object_key
                with tarfile.open(archive_path, "r:gz") as tar:
                    captured["names"] = tar.getnames()
                    manifest = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))  # type: ignore[union-attr]
                    captured["manifest"] = manifest
                return {"bucket": s3.bucket, "key": object_key, "etag": "fake-etag"}

            out = io.StringIO()
            with _cwd(root), mock.patch.object(knowledge_cli, "_upload_backup_to_s3", side_effect=fake_upload), mock.patch("sys.stdout", out):
                rc = knowledge_cli.main(["maintenance", "backup", "--json"])

            self.assertEqual(rc, 0)
            result = json.loads(out.getvalue())
            self.assertTrue(result["uploaded"])
            self.assertFalse(result["dry_run"])
            self.assertEqual(result["bucket"], "test-bucket")
            self.assertEqual(result["prefix"], "test-prefix")
            self.assertEqual(result["endpoint_url"], "http://127.0.0.1:9")
            self.assertEqual(result["region"], "test-region")
            self.assertTrue(result["object_key"].startswith("test-prefix/clawsqlite-knowledge-"))
            self.assertEqual(captured["bucket"], "test-bucket")
            self.assertEqual(captured["access_key_id"], "test-access-key")
            self.assertEqual(captured["secret_access_key"], "test-secret-key")
            self.assertIn("manifest.json", captured["names"])
            self.assertIn("db/knowledge.sqlite3", captured["names"])
            self.assertIn("articles/000001__hello.md", captured["names"])
            self.assertEqual(captured["manifest"]["includes"], ["db", "articles"])
            self.assertEqual(captured["manifest"]["object_key"], captured["object_key"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
