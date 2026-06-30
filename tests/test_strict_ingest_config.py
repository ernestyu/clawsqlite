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
import uuid
from pathlib import Path

from clawsqlite_knowledge import cli as kcli
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
def _isolated_tempdir():
    path = Path(tempfile.mkdtemp(prefix="clawsqlite_no_config_"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class StrictIngestConfigTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._env = os.environ.copy()
        self._generate_fields = kcli.generate_fields
        for key in [
            "SMALL_LLM_MODEL",
            "SMALL_LLM_BASE_URL",
            "SMALL_LLM_API_KEY",
            "EMBEDDING_MODEL",
            "EMBEDDING_BASE_URL",
            "EMBEDDING_API_KEY",
            "CLAWSQLITE_VEC_DIM",
        ]:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        if hasattr(self, "_run_cwd"):
            delattr(self, "_run_cwd")
        kcli.generate_fields = self._generate_fields
        os.environ.clear()
        os.environ.update(self._env)

    def _run_cli(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        old = Path.cwd()
        os.chdir(getattr(self, "_run_cwd", old))
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = kcli.main(argv)
        finally:
            os.chdir(old)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_missing_config_fails_before_guessing_paths(self):
        with _isolated_tempdir() as tmpdir:
            self._run_cwd = tmpdir
            code, _, err = self._run_cli(["doctor"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR_KIND: config_required", err)
        self.assertIn("clawsqlite.toml", err)

    def test_strict_ingest_rejects_explicit_heuristic_without_flag(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=False)
            self._run_cwd = root
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--text",
                    "A useful note about SQLite and agents.",
                    "--title",
                    "Strict note",
                    "--summary",
                    "Manual summary",
                    "--tags",
                    "sqlite,agent",
                    "--gen-provider",
                    "openclaw",
                    "--json",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("ERROR_KIND: llm_required", err)

    def test_strict_ingest_fails_when_required_llm_is_not_configured(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(
                root,
                require_llm=True,
                require_embedding=False,
                llm_api_key="",
            )
            self._run_cwd = root
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--text",
                    "A useful note about SQLite and agents.",
                    "--title",
                    "Strict note",
                    "--summary",
                    "Manual summary",
                    "--tags",
                    "sqlite,agent",
                    "--json",
                ]
            )
        self.assertEqual(code, 4)
        self.assertIn("ERROR_KIND: llm_generation_failed", err)

    def test_degraded_ingest_requires_explicit_allow_flag(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=False)
            self._run_cwd = root
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--text",
                    "SQLite agents need stable configuration before they write knowledge.",
                    "--title",
                    "Allowed degraded note",
                    "--gen-provider",
                    "openclaw",
                    "--allow-heuristic",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            with sqlite3.connect(root / "knowledge.sqlite3") as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT generation_quality, summary_model FROM articles WHERE id=1").fetchone()
        self.assertEqual(row["generation_quality"], "heuristic")
        self.assertEqual(row["summary_model"], "")

    def test_strict_ingest_rejects_missing_embedding(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(
                root,
                require_llm=False,
                require_embedding=True,
                embedding_api_key="",
            )
            self._run_cwd = root
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--text",
                    "Manual note with summary and tags.",
                    "--title",
                    "Embedding strict note",
                    "--summary",
                    "Manual summary",
                    "--tags",
                    "sqlite,agent",
                    "--gen-provider",
                    "off",
                    "--json",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("ERROR_KIND: embedding_required", err)

    def test_config_summary_target_controls_heuristic_summary_length(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(
                root,
                require_llm=False,
                require_embedding=False,
                summary_target_chars=140,
            )
            self._run_cwd = root
            text = " ".join([f"paragraph{i}" for i in range(120)])
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--text",
                    text,
                    "--title",
                    "Short summary note",
                    "--gen-provider",
                    "openclaw",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            with sqlite3.connect(root / "knowledge.sqlite3") as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT summary FROM articles WHERE id=1").fetchone()
        self.assertLessEqual(len(row["summary"]), 140)

    def test_strict_ingest_uses_generated_tags_and_category_over_hints(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=False)
            self._run_cwd = root

            def fake_generate(*args, **kwargs):
                self.assertEqual(kwargs["tag_count"], 8)
                self.assertIn("thought", kwargs["allowed_content_types"])
                self.assertEqual(kwargs["hint_tags"], "manual,wrong")
                return {
                    "title": "Generated title",
                    "summary": "Generated whole article summary",
                    "tags": ["sqlite", "agent", "config", "strict", "summary", "embedding", "search", "knowledge"],
                    "generation_quality": "llm",
                    "category": "thought",
                    "content_type": "thought",
                    "key_claims": ["Strict generation wins."],
                    "entities": ["ClawSQLite"],
                }

            kcli.generate_fields = fake_generate
            code, out, err = self._run_cli(
                [
                    "ingest",
                    "--text",
                    "A useful note about SQLite and agents.",
                    "--title",
                    "Human hint title",
                    "--tags-hint",
                    "manual,wrong",
                    "--category",
                    "note",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            self.assertEqual(payload["category"], "thought")
            self.assertEqual(payload["generation_quality"], "llm")
            self.assertEqual(payload["config_path"], str(config_path))
            self.assertEqual(payload["root"], str(root))
            self.assertEqual(payload["db"], str(root / "knowledge.sqlite3"))
            self.assertEqual(payload["articles_dir"], str(root / "articles"))
            self.assertFalse(payload["embedding_enabled"])
            with sqlite3.connect(root / "knowledge.sqlite3") as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT title, tags, category FROM articles WHERE id=1").fetchone()
        self.assertEqual(row["title"], "Generated title")
        self.assertEqual(row["category"], "thought")
        self.assertEqual(row["tags"], "sqlite,agent,config,strict,summary,embedding,search,knowledge")
        self.assertNotIn("manual", row["tags"])

    def test_strict_ingest_rejects_wrong_generated_tag_count(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=False)
            self._run_cwd = root

            def fake_generate(*args, **kwargs):
                return {
                    "title": "Generated title",
                    "summary": "Generated summary",
                    "tags": ["sqlite", "agent"],
                    "generation_quality": "llm",
                    "category": "note",
                    "content_type": "note",
                    "key_claims": [],
                    "entities": [],
                }

            kcli.generate_fields = fake_generate
            code, _, err = self._run_cli(["ingest", "--text", "Body", "--json"])
        self.assertEqual(code, 4)
        self.assertIn("ERROR_KIND: tags_invalid", err)

    def test_strict_ingest_rejects_category_outside_allowed_config(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=False)
            self._run_cwd = root

            def fake_generate(*args, **kwargs):
                return {
                    "title": "Generated title",
                    "summary": "Generated summary",
                    "tags": ["sqlite", "agent", "config", "strict", "summary", "embedding", "search", "knowledge"],
                    "generation_quality": "llm",
                    "category": "misc",
                    "content_type": "misc",
                    "key_claims": [],
                    "entities": [],
                }

            kcli.generate_fields = fake_generate
            code, _, err = self._run_cli(["ingest", "--text", "Body", "--json"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR_KIND: category_invalid", err)

    def test_strict_ingest_rejects_generic_generated_title(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=False)
            self._run_cwd = root

            def fake_generate(*args, **kwargs):
                return {
                    "title": "untitled",
                    "summary": "Generated summary",
                    "tags": ["sqlite", "agent", "config", "strict", "summary", "embedding", "search", "knowledge"],
                    "generation_quality": "llm",
                    "category": "note",
                    "content_type": "note",
                    "key_claims": [],
                    "entities": [],
                }

            kcli.generate_fields = fake_generate
            code, _, err = self._run_cli(["ingest", "--text", "Body", "--json"])
        self.assertEqual(code, 4)
        self.assertIn("ERROR_KIND: title_invalid", err)

    def test_doctor_reports_toml_api_key_completeness(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=True)
            self._run_cwd = root
            code, out, err = self._run_cli(["doctor", "--json"])

        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertTrue(report["llm"]["configured"])
        self.assertTrue(report["llm"]["has_api_key"])
        self.assertTrue(report["embedding"]["configured"])
        self.assertTrue(report["embedding"]["has_api_key"])
        names = [c["name"] for c in report["checks"]]
        self.assertIn("llm_config", names)
        self.assertIn("embedding_config", names)
        self.assertNotIn("llm_roundtrip", names)
        self.assertNotIn("embedding_roundtrip", names)
        self.assertFalse(report["roundtrip"]["llm_checked"])
        self.assertFalse(report["roundtrip"]["embedding_checked"])

    def test_doctor_roundtrip_checks_are_explicit(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=True, llm_api_key="")
            self._run_cwd = root
            code, out, err = self._run_cli(["doctor", "--check-llm", "--json"])

        self.assertEqual(code, 0, err)
        report = json.loads(out)
        names = [c["name"] for c in report["checks"]]
        self.assertIn("llm_config", names)
        self.assertIn("llm_roundtrip", names)
        self.assertTrue(report["roundtrip"]["llm_checked"])

    def test_reindex_fix_missing_respects_strict_llm_policy(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=False)
            self._run_cwd = root
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--text",
                    "Seed record for reindex strict policy.",
                    "--title",
                    "Seed",
                    "--gen-provider",
                    "openclaw",
                    "--allow-heuristic",
                    "--json",
                ]
            )
            self.assertEqual(code, 0, err)

            code, _, err = self._run_cli(
                [
                    "reindex",
                    "--fix-missing",
                    "--gen-provider",
                    "openclaw",
                    "--json",
                ]
            )

        self.assertEqual(code, 2)
        self.assertIn("ERROR_KIND: llm_required", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
