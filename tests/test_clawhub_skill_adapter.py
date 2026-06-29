# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import importlib.util
import os
import shutil
import subprocess
import unittest
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = REPO_ROOT / "skills" / "clawsqlite-knowledge" / "scripts" / "adapter.py"
BASE_TMP = Path(os.environ.get("CLAWSQLITE_TEST_TMP", str(REPO_ROOT / ".tmp_tests")))
BASE_TMP.mkdir(parents=True, exist_ok=True)


def _load_adapter():
    spec = importlib.util.spec_from_file_location("clawsqlite_knowledge_skill_adapter", ADAPTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _tempdir():
    path = BASE_TMP / f"tmp_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class ClawHubSkillAdapterTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.adapter = _load_adapter()
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def _fake_runner(self, captured, returncode=0, stdout='{"ok": true}', stderr=""):
        def runner(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

        return runner

    def test_config_field_is_not_allowed(self):
        result = self.adapter.handle_request(
            {"action": "search", "config": "/tmp/clawsqlite.toml", "query": "sqlite"},
            runner=self._fake_runner({}),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["kind"], "invalid_input")
        self.assertIn("config", result["error"]["message"])

    def test_rejects_path_overrides(self):
        with _tempdir() as tmpdir:
            result = self.adapter.handle_request(
                {
                    "action": "search",
                    "query": "sqlite",
                    "db": str(tmpdir / "other.sqlite3"),
                },
                runner=self._fake_runner({}),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["kind"], "path_override_forbidden")

    def test_rejects_unknown_fields(self):
        result = self.adapter.handle_request(
            {
                "action": "search",
                "query": "sqlite",
                "surprise": True,
            },
            runner=self._fake_runner({}),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["kind"], "invalid_input")
        self.assertIn("surprise", result["error"]["message"])

    def test_ingest_text_default_does_not_add_degraded_flags(self):
        captured = {}

        result = self.adapter.handle_request(
            {
                "action": "ingest_text",
                "text": "A strict note for the adapter.",
                "title": "Adapter note",
                "category": "test",
            },
            runner=self._fake_runner(captured, stdout='{"id": 1, "title": "Adapter note"}'),
        )

        argv = captured["argv"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["id"], 1)
        self.assertNotIn("--" + "config", argv)
        self.assertIn("ingest", argv)
        self.assertIn("--text", argv)
        self.assertNotIn("--allow-heuristic", argv)
        self.assertNotIn("--allow-missing-embedding", argv)
        self.assertNotIn("--gen-provider", argv)

    def test_degraded_flags_are_explicit_only(self):
        captured = {}

        self.adapter.handle_request(
            {
                "action": "ingest_text",
                "text": "A degraded no-network note.",
                "gen_provider": "off",
                "allow_heuristic": True,
                "allow_missing_embedding": True,
            },
            runner=self._fake_runner(captured),
        )

        argv = captured["argv"]
        self.assertIn("--gen-provider", argv)
        self.assertIn("off", argv)
        self.assertIn("--allow-heuristic", argv)
        self.assertIn("--allow-missing-embedding", argv)

    def test_cli_error_is_structured_from_stderr(self):
        stderr = "\n".join(
            [
                "ERROR: LLM generation is required by clawsqlite.toml.",
                "ERROR_KIND: llm_required",
                "NEXT: use the default LLM path, or explicitly pass --allow-heuristic.",
            ]
        )

        result = self.adapter.handle_request(
            {"action": "ingest_text", "text": "x"},
            runner=self._fake_runner({}, returncode=2, stdout="", stderr=stderr),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["error"]["kind"], "llm_required")
        self.assertIn("LLM generation is required", result["error"]["message"])
        self.assertIn("--allow-heuristic", result["error"]["next"])

    def test_search_builds_root_config_json_command(self):
        captured = {}

        result = self.adapter.handle_request(
            {
                "action": "search",
                "query": "sqlite agent",
                "mode": "hybrid",
                "topk": 3,
                "explain": True,
            },
            runner=self._fake_runner(captured, stdout="[]"),
        )

        argv = captured["argv"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"], [])
        self.assertNotIn("--" + "config", argv)
        self.assertIn("search", argv)
        self.assertIn("--json", argv)
        self.assertIn("--explain", argv)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
