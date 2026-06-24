# -*- coding: utf-8 -*-
"""Knowledge-layer CLI tests for clawsqlite.

这些测试比 `test_cli_smoke` 更细一点，专门覆盖
`clawsqlite knowledge` 这一层的主要命令：

- ingest（文本）
- search（基本过滤）
- show / export
- update
- delete（软删 + 硬删）
- reindex --check
- maintenance gc（dry-run + 实跑）

注意：
- 所有测试都在临时 root 目录下进行，不依赖宿主机器的现有数据；
- 不强制要求 embedding/vec0 一定可用，相关路径只做“命令可用”的验证，
  具体 embedding 行为仍由 `test_cli_smoke` 中的烟囱测试覆盖。
"""
from __future__ import annotations

import json
import os
import sqlite3
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


class KnowledgeCLITests(unittest.TestCase):
    maxDiff = None

    def _run(self, argv, *, env=None, expect_ok=True):
        env_full = os.environ.copy()
        if env:
            env_full.update(env)
        proc = subprocess.run(
            argv,
            cwd=str(REPO_ROOT),
            env=env_full,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if expect_ok and proc.returncode != 0:
            self.fail(
                f"Command failed: {' '.join(argv)}\n"
                f"exit={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        return proc

    def test_ingest_show_export_update_delete_and_maintenance(self):
        """覆盖 ingest/show/export/update/delete/maintenance 的基本行为。"""
        with _tempdir() as tmpdir:
            root = Path(tmpdir) / "kb_root"
            root.mkdir(parents=True, exist_ok=True)
            config_path = write_knowledge_config(root)

            # 1) ingest 两条记录
            def _ingest(text, title):
                cmd = [
                    PYTHON_BIN,
                    "-m",
                    "clawsqlite_cli",
                    "knowledge",
                    "ingest",
                    "--text",
                    text,
                    "--title",
                    title,
                    "--category",
                    "test",
                    "--tags",
                    "demo",
                    "--gen-provider",
                    "off",
                    "--allow-heuristic",
                    "--allow-missing-embedding",
                    "--json",
                    "--config",
                    str(config_path),
                ]
                p = self._run(cmd)
                return json.loads(p.stdout)

            row1 = _ingest("hello article 1", "Article 1")
            row2 = _ingest("hello article 2", "Article 2")
            self.assertEqual(row1["id"], 1)
            self.assertEqual(row2["id"], 2)

            # 2) search 基本验证（fts 模式）
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
                "10",
                "--json",
                "--config",
                str(config_path),
            ]
            p = self._run(search_cmd)
            res = json.loads(p.stdout)
            # 在当前测试环境下，FTS 是否命中取决于 tokenizer/语言，
            # 这里只验证搜索命令能正常返回一个 JSON list。
            self.assertIsInstance(res, list)

            # 3) show + export
            show_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "show",
                "--id",
                "1",
                "--full",
                "--json",
                "--config",
                str(config_path),
            ]
            p = self._run(show_cmd)
            show_row = json.loads(p.stdout)
            self.assertEqual(show_row["id"], 1)
            # 当前实现中，正文内容字段叫 content
            self.assertIn("content", show_row)

            out_md = root / "article1.md"
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
                "--config",
                str(config_path),
                "--json",
            ]
            p = self._run(export_cmd)
            export_info = json.loads(p.stdout)
            self.assertTrue(Path(export_info["out"]).exists())

            # 4) update（修改 title）
            update_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "update",
                "--id",
                "1",
                "--title",
                "Article 1 Updated",
                "--config",
                str(config_path),
                "--json",
            ]
            p = self._run(update_cmd)
            upd = json.loads(p.stdout)
            self.assertTrue(upd["ok"])

            # 再 show 一次确认 title 更新
            show2_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "show",
                "--id",
                "1",
                "--json",
                "--config",
                str(config_path),
            ]
            p = self._run(show2_cmd)
            show2 = json.loads(p.stdout)
            self.assertEqual(show2["title"], "Article 1 Updated")

            # 5) 软删第二条记录
            delete_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "delete",
                "--id",
                "2",
                "--config",
                str(config_path),
                "--json",
            ]
            p = self._run(delete_cmd)
            deleted = json.loads(p.stdout)
            self.assertTrue(deleted["ok"])

            # search 默认不应再返回 id=2（如果有命中的话）
            p = self._run(search_cmd)
            res2 = json.loads(p.stdout)
            ids2 = {r["id"] for r in res2}
            self.assertNotIn(2, ids2)

            # 带 --include-deleted 时，结果集应该是上述结果集的超集；
            # 如果出现 id=2，则只能出现在 include-deleted 模式中。
            search_deleted_cmd = search_cmd + ["--include-deleted"]
            p = self._run(search_deleted_cmd)
            res3 = json.loads(p.stdout)
            ids3 = {r["id"] for r in res3}
            self.assertTrue(ids2.issubset(ids3))
            if 2 in ids3:
                self.assertNotIn(2, ids2)

            # 6) reindex --check
            reindex_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "reindex",
                "--check",
                "--config",
                str(config_path),
                "--json",
            ]
            self._run(reindex_cmd)

            # 7) maintenance gc（dry-run + 实跑）
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
                "--config",
                str(config_path),
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
                "--config",
                str(config_path),
                "--json",
            ]
            p = self._run(maint_cmd)
            maint2 = json.loads(p.stdout)
            self.assertEqual(maint2["dry_run"], False)

    def test_duplicate_url_requires_update_existing(self):
        """Duplicate source_url should produce a clear actionable error."""
        with _tempdir() as tmpdir:
            root = Path(tmpdir) / "kb_root"
            root.mkdir(parents=True, exist_ok=True)
            config_path = write_knowledge_config(root)
            scraper = Path(tmpdir) / "scrape.py"
            scraper.write_text(
                "print('Title: Duplicate URL')\n"
                "print('# Duplicate URL')\n"
                "print('body')\n",
                encoding="utf-8",
            )
            url = "https://example.invalid/duplicate"
            base_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "ingest",
                "--url",
                url,
                "--scrape-cmd",
                f"{PYTHON_BIN} {scraper}",
                "--gen-provider",
                "off",
                "--allow-heuristic",
                "--allow-missing-embedding",
                "--json",
                "--config",
                str(config_path),
            ]

            first = self._run(base_cmd)
            first_row = json.loads(first.stdout)

            duplicate = self._run(base_cmd, expect_ok=False)
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("source_url already exists", duplicate.stderr)
            self.assertIn("--update-existing", duplicate.stderr)

            refresh = self._run(base_cmd + ["--update-existing"])
            refreshed_row = json.loads(refresh.stdout)
            self.assertEqual(refreshed_row["id"], first_row["id"])

    def test_maintenance_prunes_deleted_backup_files(self):
        """Soft-delete backup names should be eligible for retention pruning."""
        with _tempdir() as tmpdir:
            root = Path(tmpdir) / "kb_root"
            articles = root / "articles"
            articles.mkdir(parents=True, exist_ok=True)
            config_path = write_knowledge_config(root)
            old_backup = articles / "000001__old.md.bak_deleted_20000101000000"
            old_backup.write_text("old backup", encoding="utf-8")

            maint_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "maintenance",
                "prune",
                "--days",
                "0",
                "--config",
                str(config_path),
                "--json",
            ]
            p = self._run(maint_cmd)
            out = json.loads(p.stdout)
            self.assertIn(str(old_backup), out["deleted"])
            self.assertFalse(old_backup.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
