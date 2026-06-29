# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
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


class StrictIngestConfigTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._env = os.environ.copy()
        for key in [
            "CLAWSQLITE_CONFIG",
            "CLAWSQLITE_ROOT",
            "CLAWSQLITE_DB",
            "CLAWSQLITE_ARTICLES_DIR",
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
        os.environ.clear()
        os.environ.update(self._env)

    def _run_cli(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = kcli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_missing_config_fails_before_guessing_paths(self):
        with _tempdir() as tmpdir:
            old = Path.cwd()
            os.chdir(tmpdir)
            try:
                code, _, err = self._run_cli(["doctor"])
            finally:
                os.chdir(old)
        self.assertEqual(code, 2)
        self.assertIn("ERROR_KIND: config_required", err)
        self.assertIn("clawsqlite.toml", err)

    def test_strict_ingest_rejects_explicit_heuristic_without_flag(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=False)
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--config",
                    str(config_path),
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
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--config",
                    str(config_path),
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
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--config",
                    str(config_path),
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
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--config",
                    str(config_path),
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
            text = " ".join([f"paragraph{i}" for i in range(120)])
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--config",
                    str(config_path),
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

    def test_doctor_reports_toml_api_key_sources(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=True)
            code, out, err = self._run_cli(["doctor", "--config", str(config_path), "--json"])

        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertTrue(report["llm"]["configured"])
        self.assertEqual(report["llm"]["api_key_source"], "config")
        self.assertTrue(report["embedding"]["configured"])
        self.assertEqual(report["embedding"]["api_key_source"], "config")

    def test_reindex_fix_missing_respects_strict_llm_policy(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=False)
            code, _, err = self._run_cli(
                [
                    "ingest",
                    "--config",
                    str(config_path),
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
                    "--config",
                    str(config_path),
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
