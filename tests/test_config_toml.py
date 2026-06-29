# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import os
import shutil
import unittest
import uuid
from pathlib import Path

from clawsqlite_knowledge.config import ConfigError, apply_config_env, find_config_path, load_knowledge_config
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


class KnowledgeConfigTomlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_find_config_walks_up_from_cwd(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root)
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            found = find_config_path(nested)
            self.assertEqual(found, config_path)

    def test_load_config_resolves_paths_and_policy(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=True, summary_target_chars=640)
            cfg = load_knowledge_config(cli_config=str(config_path))
            self.assertEqual(cfg.root, str(root))
            self.assertEqual(cfg.db, str(root / "knowledge.sqlite3"))
            self.assertEqual(cfg.articles_dir, str(root / "articles"))
            self.assertTrue(cfg.ingest.require_llm)
            self.assertTrue(cfg.ingest.require_embedding)
            self.assertEqual(cfg.ingest.summary_target_chars, 640)
            self.assertEqual(cfg.llm.context_window_chars, 4000)
            self.assertEqual(cfg.llm.resolved_api_key, "test-small-llm-key")
            self.assertEqual(cfg.llm.api_key_source, "config")
            self.assertEqual(cfg.embedding.resolved_api_key, "test-embedding-key")
            self.assertEqual(cfg.embedding.api_key_source, "config")

    def test_config_api_keys_are_applied_as_runtime_values(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(
                root,
                llm_api_key="llm-from-toml",
                embedding_api_key="embedding-from-toml",
            )
            os.environ["SMALL_LLM_API_KEY"] = "stale-env-llm"
            os.environ["EMBEDDING_API_KEY"] = "stale-env-embedding"

            cfg = load_knowledge_config(cli_config=str(config_path))
            apply_config_env(cfg)

            self.assertEqual(os.environ["SMALL_LLM_API_KEY"], "llm-from-toml")
            self.assertEqual(os.environ["EMBEDDING_API_KEY"], "embedding-from-toml")

    def test_config_clears_stale_runtime_keys_when_api_keys_missing(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(
                root,
                llm_api_key="",
                embedding_api_key="",
            )
            os.environ["SMALL_LLM_API_KEY"] = "stale-env-llm"
            os.environ["EMBEDDING_API_KEY"] = "stale-env-embedding"

            cfg = load_knowledge_config(cli_config=str(config_path))
            apply_config_env(cfg)

            self.assertNotIn("SMALL_LLM_API_KEY", os.environ)
            self.assertNotIn("EMBEDDING_API_KEY", os.environ)

    def test_relative_root_resolves_from_config_directory(self):
        with _tempdir() as tmpdir:
            project = tmpdir / "project"
            project.mkdir()
            config_path = project / "clawsqlite.toml"
            config_path.write_text(
                """
[knowledge]
root = "./knowledge_data"
db = "knowledge.sqlite3"
articles_dir = "articles"
""".lstrip(),
                encoding="utf-8",
            )

            cfg = load_knowledge_config(cli_config=str(config_path))

            self.assertEqual(cfg.root, str(project / "knowledge_data"))
            self.assertEqual(cfg.db, str(project / "knowledge_data" / "knowledge.sqlite3"))

    def test_cli_overrides_paths_without_changing_config_file(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root)
            override = tmpdir / "override"
            cfg = load_knowledge_config(cli_config=str(config_path), cli_root=str(override))
            self.assertEqual(cfg.root, str(override))
            self.assertEqual(cfg.db, str(override / "knowledge.sqlite3"))

    def test_config_rejects_non_fail_fallback_policy(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root)
            text = config_path.read_text(encoding="utf-8").replace('fallback = "fail"', 'fallback = "heuristic"')
            config_path.write_text(text, encoding="utf-8")

            with self.assertRaises(ConfigError):
                load_knowledge_config(cli_config=str(config_path))

    def test_config_file_requires_root_even_when_env_root_exists(self):
        with _tempdir() as tmpdir:
            config_path = tmpdir / "clawsqlite.toml"
            config_path.write_text("[knowledge]\ndb = \"knowledge.sqlite3\"\n", encoding="utf-8")
            os.environ["CLAWSQLITE_ROOT"] = str(tmpdir / "env_root")

            with self.assertRaises(ConfigError):
                load_knowledge_config(cli_config=str(config_path))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
