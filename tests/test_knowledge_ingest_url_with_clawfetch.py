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
- 真实 URL 选用的是公开微信文章：
  https://mp.weixin.qq.com/s/UzgKeQwWWoV4v884l_jcrg
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


class KnowledgeIngestURLWithClawfetchTests(unittest.TestCase):
    maxDiff = None

    def _run(self, argv, *, cwd: Path, env=None, expect_ok=True):
        env_full = os.environ.copy()
        if env:
            env_full.update(env)
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
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
        wechat_url = "https://mp.weixin.qq.com/s/UzgKeQwWWoV4v884l_jcrg"

        # 1) 用 clawfetch skill 抓取网页为 markdown
        skills_root = REPO_ROOT.parent / "clawhub-skills" / "clawfetch"
        self.assertTrue(skills_root.exists(), "clawfetch skill directory missing")

        out_md = Path("/tmp/clawfetch-wechat-test.md")
        fetch_cmd = [
            "node",
            "node_modules/clawfetch/clawfetch.js",
            wechat_url,
        ]
        p = self._run(fetch_cmd, cwd=skills_root)
        # 将 stdout 写入临时文件，便于后续调试和 ingest
        out_md.write_text(p.stdout, encoding="utf-8")

        # 2) 读取抓取到的 markdown 内容，作为文本入库
        content = out_md.read_text(encoding="utf-8")
        self.assertIn("--- METADATA ---", content)
        self.assertIn("--- MARKDOWN ---", content)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "kb_root"
            root.mkdir(parents=True, exist_ok=True)

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
                "--json",
                "--root",
                str(root),
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
                "--root",
                str(root),
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
                "--root",
                str(root),
                "--json",
            ]
            p_exp = self._run(export_cmd, cwd=REPO_ROOT)
            info = json.loads(p_exp.stdout)
            self.assertTrue(Path(info["out"]).exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
