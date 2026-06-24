# -*- coding: utf-8 -*-
"""Integration test: clawfetch + clawsqlite knowledge ingest (URL).

使用 clawhub-skills/clawfetch 这个 skill 里的 clawfetch CLI 抓取一篇
真实的微信文章，然后把生成的 markdown 通过 `clawsqlite knowledge ingest`
以文本形式写入知识库，验证整个链路：

- clawfetch 抓取 → 标准 metadata + markdown；
- ingest --text → 创建 DB 记录 + markdown 文件；
- show/export 等命令能正常访问到这条记录。

注意：
- 这里不直接走 ingest --url + --scrape-cmd，而是显式分成两步：
  抓取 → 文本 ingest，便于在测试中检查中间产物；
- URL 可通过环境变量 `CLAWSQLITE_TEST_WECHAT_URL` 覆盖。
- 该测试依赖外部工具（node + clawhub-skills/clawfetch）以及可用的网络。
  默认情况下会跳过；设置 `CLAWSQLITE_RUN_WECHAT_TESTS=1` 才会执行。
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


class KnowledgeIngestURLWithClawfetchTests(unittest.TestCase):
    maxDiff = None

    def _run(self, argv, *, cwd: Path, env=None, expect_ok=True):
        env_full = os.environ.copy()
        if env:
            env_full.update(env)
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
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

    def test_wechat_article_via_clawfetch_and_knowledge_ingest(self):
        """抓取真实微信文章并通过 ingest --text 写入知识库。"""
        if os.environ.get("CLAWSQLITE_RUN_WECHAT_TESTS", "").strip() != "1":
            self.skipTest("Set CLAWSQLITE_RUN_WECHAT_TESTS=1 to enable live WeChat + clawfetch integration test")

        wechat_url = os.environ.get(
            "CLAWSQLITE_TEST_WECHAT_URL",
            "https://mp.weixin.qq.com/s/7GQpp2TzkF6GLeKOuOeF6Q",
        )

        # 1) 用 clawfetch skill 抓取网页为 markdown
        skills_root = Path(
            os.environ.get(
                "CLAWSQLITE_TEST_CLAWFETCH_ROOT",
                str(REPO_ROOT.parent / "clawhub-skills" / "clawfetch"),
            )
        )
        if not skills_root.exists():
            self.skipTest(f"clawfetch skill directory missing: {skills_root}")
        if shutil.which("node") is None:
            self.skipTest("node is not available on PATH")

        clawfetch_js = skills_root / "node_modules" / "clawfetch" / "clawfetch.js"
        if not clawfetch_js.exists():
            self.skipTest("clawfetch is not installed; run npm install in clawhub-skills/clawfetch")

        fetch_cmd = [
            "node",
            str(clawfetch_js),
            wechat_url,
        ]
        p = self._run(fetch_cmd, cwd=skills_root)

        # 2) 读取抓取到的 markdown 内容，作为文本入库
        content = p.stdout
        self.assertIn("--- METADATA ---", content)
        self.assertIn("--- MARKDOWN ---", content)

        with _tempdir() as tmpdir:
            root = Path(tmpdir) / "kb_root"
            root.mkdir(parents=True, exist_ok=True)
            config_path = write_knowledge_config(root)

            ingest_cmd = [
                PYTHON_BIN,
                "-m",
                "clawsqlite_cli",
                "knowledge",
                "ingest",
                "--text",
                content,
                "--title",
                "微信文章: Ground Station 项目",
                "--category",
                "web",
                "--tags",
                "wechat,ground-station",
                "--gen-provider",
                "off",
                "--allow-heuristic",
                "--allow-missing-embedding",
                "--json",
                "--config",
                str(config_path),
            ]
            p_ing = self._run(ingest_cmd, cwd=REPO_ROOT)
            row = json.loads(p_ing.stdout)
            self.assertEqual(row["id"], 1)
            self.assertIn("local_file_path", row)

            # 3) show/export 检查这条记录
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
            p_show = self._run(show_cmd, cwd=REPO_ROOT)
            rec = json.loads(p_show.stdout)
            self.assertEqual(rec["id"], 1)
            self.assertEqual(rec["category"], "web")
            self.assertIn("content", rec)

            export_path = root / "wechat_article.md"
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
                str(export_path),
                "--config",
                str(config_path),
                "--json",
            ]
            p_exp = self._run(export_cmd, cwd=REPO_ROOT)
            info = json.loads(p_exp.stdout)
            self.assertTrue(Path(info["out"]).exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
