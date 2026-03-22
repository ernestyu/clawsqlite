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
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

# Prefer the venv python if available (matches how we run other tooling).
DEFAULT_PY = "/opt/venv/bin/python"
PYTHON_BIN = os.environ.get("CLAWSQLITE_PYTHON", DEFAULT_PY)


class CLISmokeTests(unittest.TestCase):
    """End-to-end CLI tests for clawsqlite.

    这些测试偏向“从外向内”的烟囱测试，目标是保证：

    - `clawsqlite knowledge` 各主要子命令在一个临时 root 下能跑通；
    - plumbing 层 (`clawsqlite db ...` 等) 在真实 DB 上能工作；
    - 对 Embedding / vec0 / tokenizer 的依赖以“尽量不 hard-fail”为原则，
      在依赖缺失时不会导致 Python 堆栈直接崩溃。
    """

    maxDiff = None

    def _run(self, argv, *, env=None, expect_ok=True):
        """Run a command under repo root and optionally assert success."""
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        proc = subprocess.run(
            argv,
            cwd=str(REPO_ROOT),
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
        """End-to-end smoke test for knowledge CLI + basic plumbing commands."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "kb_root"
            db_path = root / "knowledge.sqlite3"

            # Ensure root exists; CLI will create db + articles under it.
            root.mkdir(parents=True, exist_ok=True)

            # 1) Ingest a simple text article
            ingest_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "ingest",
                "--text",
                "hello clawsqlite",
                "--title",
                "Hello",
                "--category",
                "test",
                "--tags",
                "demo",
                "--gen-provider",
                "off",
                "--json",
                "--root",
                str(root),
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
                "search",
                "hello",
                "--mode",
                "fts",
                "--topk",
                "5",
                "--json",
                "--root",
                str(root),
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
                "show",
                "--id",
                "1",
                "--json",
                "--root",
                str(root),
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
                "export",
                "--id",
                "1",
                "--format",
                "md",
                "--out",
                str(out_md),
                "--root",
                str(root),
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
                "update",
                "--id",
                "1",
                "--title",
                "Hello Updated",
                "--root",
                str(root),
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
                "reindex",
                "--check",
                "--root",
                str(root),
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
                "gc",
                "--days",
                "0",
                "--dry-run",
                "--root",
                str(root),
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
                "gc",
                "--days",
                "0",
                "--root",
                str(root),
                "--json",
            ]
            p = self._run(maint_cmd)
            maint2 = json.loads(p.stdout)
            self.assertEqual(maint2["dry_run"], False)

            # 8) embed-from-summary 命令存在且可调用。
            #    在默认测试环境下未必配置了 embedding / vec0 表，
            #    所以只做“命令可以跑起来”的烟囱测试，不强求成功。
            embed_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "embed-from-summary",
                "--root",
                str(root),
            ]
            self._run(embed_cmd, expect_ok=False)

            # 9) Plumbing: db schema should work on the same DB
            db_schema_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "db",
                "schema",
                "--db",
                str(db_path),
            ]
            self._run(db_schema_cmd)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
