# -*- coding: utf-8 -*-
"""Minimal CLI smoke tests for clawsqlite + knowledge.

These tests intentionally exercise the main user-facing commands end-to-end
against a temporary root directory, without relying on any pre-existing
DB/files under the repo.

They are deliberately small and environment-aware:
- Do not require embedding to be configured.
- Do not assume FTS tokenizer/vec extensions beyond what knowledge itself
  uses when ingesting/searching.

Run with:

    python -m unittest tests.test_cli_smoke

or any test runner that understands unittest-style tests.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import contextlib
import shutil
import unittest
import uuid
from pathlib import Path

from tests.helpers import write_knowledge_config


REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_TMP = Path(os.environ.get("CLAWSQLITE_TEST_TMP", str(REPO_ROOT / ".tmp_tests")))
BASE_TMP.mkdir(parents=True, exist_ok=True)

# Prefer the venv python if available (matches how we run other tooling).
DEFAULT_PY = sys.executable
PYTHON_BIN = os.environ.get("CLAWSQLITE_PYTHON", DEFAULT_PY)

@contextlib.contextmanager
def _tempdir():
    path = BASE_TMP / f"tmp_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class CLISmokeTests(unittest.TestCase):
    """End-to-end CLI tests for clawsqlite.

    这些测试偏向“从外向内”的烟囱测试，目标是保证：

    - `clawsqlite knowledge` 各主要子命令在一个临时 root 下能跑通；
    - admin 层 (`clawsqlite admin db ...` 等) 在真实 DB 上能工作；
    - 对 Embedding / vec0 / tokenizer 的依赖以“尽量不 hard-fail”为原则，
      在依赖缺失时不会导致 Python 堆栈直接崩溃。
    """

    maxDiff = None

    def _run(self, argv, *, env=None, expect_ok=True):
        """Run a command under repo root and optionally assert success."""
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        existing = full_env.get("PYTHONPATH", "")
        full_env["PYTHONPATH"] = str(REPO_ROOT) if not existing else str(REPO_ROOT) + os.pathsep + existing
        proc = subprocess.run(
            argv,
            cwd=str(getattr(self, "_run_cwd", REPO_ROOT)),
            env=full_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if expect_ok:
            if proc.returncode != 0:
                self.fail(
                    f"Command failed: {' '.join(argv)}\n"
                    f"exit={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
                )
        return proc

    def test_knowledge_and_plumbing_smoke(self):
        """End-to-end smoke test for knowledge CLI + basic admin commands."""
        with _tempdir() as tmpdir:
            root = Path(tmpdir) / "kb_root"
            db_path = root / "knowledge.sqlite3"

            # Ensure root exists; CLI will create db + articles under it.
            root.mkdir(parents=True, exist_ok=True)
            config_path = write_knowledge_config(root)
            self._run_cwd = root

            # 1) Ingest a simple text article
            ingest_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "record",
                "ingest",
                "--text",
                "hello clawsqlite",
                "--title",
                "Hello",
                "--category",
                "test",
                "--gen-provider",
                "off",
                "--allow-heuristic",
                "--allow-missing-embedding",
                "--json",
            ]
            p = self._run(ingest_cmd)
            data = json.loads(p.stdout)
            self.assertEqual(data["id"], 1)
            self.assertIn("local_file_path", data)

            # 2) Search it back (FTS mode)
            search_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "record",
                "search",
                "hello",
                "--mode",
                "fts",
                "--topk",
                "5",
                "--json",
            ]
            p = self._run(search_cmd)
            res = json.loads(p.stdout)
            self.assertIsInstance(res, list)
            self.assertGreaterEqual(len(res), 1)
            self.assertEqual(res[0]["id"], 1)

            # 3) Show the record (JSON)
            show_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "record",
                "show",
                "--id",
                "1",
                "--json",
            ]
            p = self._run(show_cmd)
            row = json.loads(p.stdout)
            self.assertEqual(row["id"], 1)
            self.assertEqual(row["title"], "Hello")

            # 4) Export as markdown
            out_md = root / "export.md"
            export_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "record",
                "export",
                "--id",
                "1",
                "--format",
                "md",
                "--out",
                str(out_md),
                "--json",
            ]
            p = self._run(export_cmd)
            exp_info = json.loads(p.stdout)
            self.assertTrue(Path(exp_info["out"]).exists())

            # 5) Update title
            update_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "record",
                "update",
                "--id",
                "1",
                "--title",
                "Hello Updated",
                "--json",
            ]
            p = self._run(update_cmd)
            upd = json.loads(p.stdout)
            self.assertTrue(upd["ok"])

            # 6) Reindex check (no-op but should succeed)
            reindex_check_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "maintenance",
                "reindex",
                "--check",
                "--json",
            ]
            self._run(reindex_check_cmd)

            # 7) Maintenance dry-run and real run (practically no-op on fresh root)
            maint_dry_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "maintenance",
                "cleanup",
                "--days",
                "0",
                "--dry-run",
                "--json",
            ]
            p = self._run(maint_dry_cmd)
            maint = json.loads(p.stdout)
            self.assertEqual(maint["dry_run"], True)

            maint_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "maintenance",
                "cleanup",
                "--days",
                "0",
                "--json",
            ]
            p = self._run(maint_cmd)
            maint2 = json.loads(p.stdout)
            self.assertEqual(maint2["dry_run"], False)

            # 8) Knowledge-level corpus backup is config-driven and can be
            # dry-run without touching the configured S3 target.
            backup_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "maintenance",
                "backup",
                "--dry-run",
                "--json",
            ]
            p = self._run(backup_cmd)
            backup = json.loads(p.stdout)
            self.assertTrue(backup["dry_run"])
            self.assertFalse(backup["uploaded"])
            self.assertEqual(backup["provider"], "s3")
            self.assertEqual(backup["bucket"], "test-bucket")
            self.assertIn("db", backup["includes"])
            self.assertIn("articles", backup["includes"])

            # 9) Admin: db schema should work on the configured component DB
            db_schema_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "admin",
                "db",
                "schema",
            ]
            self._run(db_schema_cmd)

    def test_top_level_help_exposes_knowledge_and_admin_only(self):
        p = self._run([PYTHON_BIN, "-m", "clawsqlite_cli", "--help"])
        self.assertIn("knowledge", p.stdout)
        self.assertIn("admin", p.stdout)
        self.assertNotIn("db         Low-level", p.stdout)

        p_admin = self._run([PYTHON_BIN, "-m", "clawsqlite_cli", "admin", "--help"])
        self.assertIn("current knowledge component", p_admin.stdout)
        self.assertIn("same clawsqlite.toml", p_admin.stdout)
        self.assertIn("db", p_admin.stdout)
        self.assertIn("index", p_admin.stdout)
        self.assertIn("fs", p_admin.stdout)
        self.assertIn("embed", p_admin.stdout)

    def test_old_low_level_top_level_namespace_is_removed(self):
        p = self._run([PYTHON_BIN, "-m", "clawsqlite_cli", "db", "--help"], expect_ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("unknown namespace", p.stderr)
        self.assertIn("clawsqlite --help", p.stderr)

    def test_removed_knowledge_implementation_commands_are_not_exposed(self):
        p_help = self._run([PYTHON_BIN, "-m", "clawsqlite_cli", "knowledge", "--help"])
        self.assertIn("record", p_help.stdout)
        self.assertIn("maintenance", p_help.stdout)
        self.assertIn("analysis", p_help.stdout)
        self.assertNotIn("embed-from-summary", p_help.stdout)
        self.assertNotIn("rebuild-quality", p_help.stdout)
        self.assertNotIn("ingest       Ingest", p_help.stdout)

        p_ingest_help = self._run([PYTHON_BIN, "-m", "clawsqlite_cli", "knowledge", "record", "ingest", "--help"])
        self.assertNotIn("--tags-hint", p_ingest_help.stdout)

        p_backup_help = self._run([PYTHON_BIN, "-m", "clawsqlite_cli", "knowledge", "maintenance", "backup", "--help"])
        self.assertIn("--dry-run", p_backup_help.stdout)
        self.assertNotIn("--out", p_backup_help.stdout)
        self.assertNotIn("--db-only", p_backup_help.stdout)

        p_embed = self._run([PYTHON_BIN, "-m", "clawsqlite_cli", "knowledge", "embed-from-summary"], expect_ok=False)
        self.assertNotEqual(p_embed.returncode, 0)
        self.assertIn("invalid choice", p_embed.stderr)

        p_quality = self._run([PYTHON_BIN, "-m", "clawsqlite_cli", "knowledge", "rebuild-quality"], expect_ok=False)
        self.assertNotEqual(p_quality.returncode, 0)
        self.assertIn("invalid choice", p_quality.stderr)

    def test_legacy_flat_knowledge_commands_fail_without_rewrite(self):
        with _tempdir() as tmpdir:
            self._run_cwd = tmpdir
            for cmd, replacement in [
                ("init-config", "clawsqlite knowledge maintenance init-config"),
                ("ingest", "clawsqlite knowledge record ingest"),
                ("search", "clawsqlite knowledge record search"),
                ("show", "clawsqlite knowledge record show"),
                ("export", "clawsqlite knowledge record export"),
                ("update", "clawsqlite knowledge record update"),
                ("delete", "clawsqlite knowledge record delete"),
                ("doctor", "clawsqlite knowledge maintenance doctor"),
                ("reindex", "clawsqlite knowledge maintenance reindex"),
                ("build-interest-clusters", "clawsqlite knowledge analysis build-interest-clusters"),
                ("inspect-interest-clusters", "clawsqlite knowledge analysis inspect-interest-clusters"),
                ("report-interest", "clawsqlite knowledge analysis report-interest"),
            ]:
                proc = self._run([
                    PYTHON_BIN,
                    "-m",
                    "clawsqlite_cli",
                    "knowledge",
                    cmd,
                    "--help",
                ], expect_ok=False)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("legacy flat knowledge commands are no longer supported", proc.stderr)
                self.assertIn(replacement, proc.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
