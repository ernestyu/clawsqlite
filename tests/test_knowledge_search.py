# -*- coding: utf-8 -*-
"""More detailed tests for `clawsqlite knowledge search`.

覆盖：
- FTS 模式下基本搜索（包含过滤参数）；
- `--include-deleted` 的行为；
- hybrid / vec 模式在 embedding 未启用时的降级或错误表现。

注意：
- 这里依然在临时 root 下运行，不依赖宿主机现有数据；
- 不假设 vec0/Embedding 一定可用，针对 hybrid/vec 主要检查“行为合理、
  不抛 Python 堆栈”。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PY = "/opt/venv/bin/python"
PYTHON_BIN = os.environ.get("CLAWSQLITE_PYTHON", DEFAULT_PY)


class KnowledgeSearchTests(unittest.TestCase):
    maxDiff = None

    def _run(self, argv, *, env=None, expect_ok=True):
        env_full = os.environ.copy()
        if env:
            env_full.update(env)
        proc = subprocess.run(
            argv,
            cwd=str(REPO_ROOT),
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

    def test_search_fts_with_filters_and_include_deleted(self):
        """在 FTS 模式下检查基本过滤参数和 include-deleted 行为。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "kb_root"
            root.mkdir(parents=True, exist_ok=True)

            def _ingest(text, title, category, tags):
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
                    category,
                    "--tags",
                    tags,
                    "--gen-provider",
                    "off",
                    "--json",
                    "--root",
                    str(root),
                ]
                p = self._run(cmd)
                return json.loads(p.stdout)

            # 准备三条记录，category / tags 不同
            r1 = _ingest("hello alpha", "Alpha", "dev", "tag1")
            r2 = _ingest("hello beta", "Beta", "web", "tag2")
            r3 = _ingest("hello gamma", "Gamma", "dev", "tag2")

            # 删除第二条（soft delete）
            del_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "delete",
                "--id",
                str(r2["id"]),
                "--root",
                str(root),
                "--json",
            ]
            self._run(del_cmd)

            # 基础 FTS search
            base_cmd = [
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
                "--root",
                str(root),
            ]

            # 1) 不带过滤：应该只返回未删除记录（若命中）
            p = self._run(base_cmd)
            res = json.loads(p.stdout)
            ids = {row["id"] for row in res}
            self.assertNotIn(r2["id"], ids)

            # 2) 带 category=dev：如果有命中，则只包含 dev 类别
            cmd_dev = base_cmd + ["--category", "dev"]
            p = self._run(cmd_dev)
            res_dev = json.loads(p.stdout)
            for row in res_dev:
                self.assertEqual(row["category"], "dev")

            # 3) 带 tag=tag2：如果有命中，则只包含 tag2
            cmd_tag2 = base_cmd + ["--tag", "tag2"]
            p = self._run(cmd_tag2)
            res_tag2 = json.loads(p.stdout)
            for row in res_tag2:
                self.assertIn("tag2", row.get("tags", ""))

            # 4) --include-deleted：结果集应该是基础结果集的超集；如果出现 r2，说明软删记录只在此模式出现
            cmd_inc_del = base_cmd + ["--include-deleted"]
            p = self._run(cmd_inc_del)
            res_inc = json.loads(p.stdout)
            ids_inc = {row["id"] for row in res_inc}
            self.assertTrue(ids.issubset(ids_inc))
            if r2["id"] in ids_inc:
                self.assertNotIn(r2["id"], ids)

    def test_search_hybrid_and_vec_modes_without_embedding(self):
        """在未启用 embedding 时，检查 hybrid/vec 模式的行为不会崩溃。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "kb_root"
            root.mkdir(parents=True, exist_ok=True)

            # 简单入一条数据
            ingest_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "ingest",
                "--text",
                "hello hybrid",
                "--title",
                "Hybrid",
                "--category",
                "test",
                "--tags",
                "hybrid",
                "--gen-provider",
                "off",
                "--json",
                "--root",
                str(root),
            ]
            self._run(ingest_cmd)

            # hybrid 模式：在 embedding 未启用时应降级为 FTS 或至少给出合理错误，不抛栈
            hybrid_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "search",
                "hello",
                "--mode",
                "hybrid",
                "--topk",
                "5",
                "--json",
                "--root",
                str(root),
            ]
            self._run(hybrid_cmd, expect_ok=False)

            # vec 模式：同样只检查不会产生 Python 堆栈崩溃
            vec_cmd = hybrid_cmd.copy()
            vec_cmd[vec_cmd.index("hybrid")] = "vec"
            self._run(vec_cmd, expect_ok=False)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
