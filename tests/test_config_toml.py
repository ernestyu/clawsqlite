# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import os
import shutil
import unittest
import uuid
from pathlib import Path

from clawsqlite_knowledge import db as dbmod
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


@contextlib.contextmanager
def _cwd(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class KnowledgeConfigTomlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()
        self._find_vec0_so = dbmod._find_vec0_so

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        dbmod._find_vec0_so = self._find_vec0_so

    def test_find_config_uses_only_current_instance_home(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root)
            nested = root / "a" / "b"
            nested.mkdir(parents=True)
            self.assertEqual(find_config_path(root), config_path)
            self.assertIsNone(find_config_path(nested))

    def test_load_config_resolves_paths_and_policy(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root, require_llm=True, require_embedding=True, summary_target_chars=640)
            with _cwd(root):
                cfg = load_knowledge_config()
            self.assertEqual(cfg.root, str(root))
            self.assertEqual(cfg.config_resolution_mode, "knowledge_instance_home_config")
            self.assertIn("current knowledge instance home", cfg.config_source_reason)
            self.assertEqual(cfg.db, str(root / "knowledge.sqlite3"))
            self.assertEqual(cfg.articles_dir, str(root / "articles"))
            self.assertTrue(cfg.ingest.require_llm)
            self.assertTrue(cfg.ingest.require_embedding)
            self.assertEqual(cfg.ingest.summary_target_chars, 640)
            self.assertEqual(cfg.ingest.tag_count, 8)
            self.assertIn("note", cfg.ingest.allowed_categories)
            self.assertEqual(cfg.llm.context_window_chars, 4000)
            self.assertEqual(cfg.llm.resolved_api_key, "test-llm-key")
            self.assertEqual(cfg.embedding.resolved_api_key, "test-embedding-key")
            self.assertEqual(cfg.backup.provider, "s3")
            self.assertEqual(cfg.backup.s3.bucket, "test-bucket")
            self.assertEqual(cfg.backup.s3.prefix, "test-prefix")
            self.assertEqual(cfg.backup.s3.endpoint_url, "http://127.0.0.1:9")
            self.assertEqual(cfg.backup.s3.region, "test-region")
            self.assertEqual(cfg.backup.s3.access_key_id, "test-access-key")

    def test_config_api_keys_are_applied_as_runtime_values(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(
                root,
                llm_api_key="llm-from-toml",
                embedding_api_key="embedding-from-toml",
            )
            os.environ["LLM_API_KEY"] = "stale-env-llm"
            os.environ["EMBEDDING_API_KEY"] = "stale-env-embedding"

            with _cwd(root):
                cfg = load_knowledge_config()
            apply_config_env(cfg)

            self.assertEqual(os.environ["LLM_API_KEY"], "llm-from-toml")
            self.assertEqual(os.environ["EMBEDDING_API_KEY"], "embedding-from-toml")

    def test_apply_config_env_auto_discovers_vec_extension_for_admin(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            write_knowledge_config(root)
            dbmod._find_vec0_so = lambda: "/app/node_modules/sqlite-vec-linux-x64/vec0.so"  # type: ignore[assignment]
            os.environ.pop("CLAWSQLITE_VEC_EXT", None)

            with _cwd(root):
                cfg = load_knowledge_config()
            apply_config_env(cfg)

            self.assertEqual(os.environ["CLAWSQLITE_VEC_EXT"], "/app/node_modules/sqlite-vec-linux-x64/vec0.so")

    def test_apply_config_env_preserves_explicit_vec_extension_override(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            write_knowledge_config(root)
            dbmod._find_vec0_so = lambda: "/app/node_modules/sqlite-vec-linux-x64/vec0.so"  # type: ignore[assignment]
            os.environ["CLAWSQLITE_VEC_EXT"] = "/debug/vec0.so"

            with _cwd(root):
                cfg = load_knowledge_config()
            apply_config_env(cfg)

            self.assertEqual(os.environ["CLAWSQLITE_VEC_EXT"], "/debug/vec0.so")

    def test_config_clears_stale_runtime_keys_when_api_keys_missing(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(
                root,
                llm_api_key="",
                embedding_api_key="",
            )
            os.environ["LLM_API_KEY"] = "stale-env-llm"
            os.environ["EMBEDDING_API_KEY"] = "stale-env-embedding"

            with _cwd(root):
                cfg = load_knowledge_config()
            apply_config_env(cfg)

            self.assertNotIn("LLM_API_KEY", os.environ)
            self.assertNotIn("EMBEDDING_API_KEY", os.environ)

    def test_search_interest_and_report_config_apply_to_runtime(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root)
            with config_path.open("a", encoding="utf-8") as f:
                f.write(
                    """

[fts]
jieba = "on"

[search.query]
tag_min = 6
tag_max = 9

[search.weights.mode1]
vec = 0.30
fts = 0.10
tag = 0.55
priority = 0.03
recency = 0.02

[search.tag]
vec_fraction = 0.82
fts_log_alpha = 3.5

[interest]
cluster_algo = "hierarchical"
tag_weight = 0.66
use_pca = false
pca_explained_variance_threshold = 0.90
min_size = 4
max_clusters = 22
kmeans_random_state = 7
kmeans_n_init = 3
kmeans_max_iter = 111
enable_post_merge = false
merge_distance_threshold = 0.07
hierarchical_linkage = "complete"
hierarchical_distance_threshold = 0.18
merge_alpha = 0.33

[report]
lang = "zh"
""",
                )

            with _cwd(root):
                cfg = load_knowledge_config()
            apply_config_env(cfg)

        self.assertEqual(cfg.fts.jieba, "on")
        self.assertEqual(cfg.search.query_tag_min, 6)
        self.assertEqual(cfg.search.query_tag_max, 9)
        self.assertAlmostEqual(cfg.search.weights_mode1["tag"], 0.55)
        self.assertEqual(cfg.interest.cluster_algo, "hierarchical")
        self.assertFalse(cfg.interest.use_pca)
        self.assertEqual(cfg.report.lang, "zh")
        self.assertEqual(os.environ["CLAWSQLITE_FTS_JIEBA"], "on")
        self.assertEqual(os.environ["CLAWSQLITE_SEARCH_QUERY_TAG_MIN"], "6")
        self.assertEqual(os.environ["CLAWSQLITE_SEARCH_QUERY_TAG_MAX"], "9")
        self.assertIn("tag=0.55", os.environ["CLAWSQLITE_SCORE_WEIGHTS_MODE1"])
        self.assertEqual(os.environ["CLAWSQLITE_TAG_VEC_FRACTION"], "0.82")
        self.assertEqual(os.environ["CLAWSQLITE_INTEREST_CLUSTER_ALGO"], "hierarchical")
        self.assertEqual(os.environ["CLAWSQLITE_INTEREST_USE_PCA"], "false")
        self.assertEqual(os.environ["CLAWSQLITE_REPORT_LANG"], "zh")

    def test_config_rejects_root_outside_instance_home(self):
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

            with self.assertRaises(ConfigError) as ctx:
                with _cwd(project):
                    load_knowledge_config()
            self.assertIn("knowledge instance home", str(ctx.exception))

    def test_config_rejects_non_fail_fallback_policy(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root)
            text = config_path.read_text(encoding="utf-8").replace('fallback = "fail"', 'fallback = "heuristic"')
            config_path.write_text(text, encoding="utf-8")

            with self.assertRaises(ConfigError):
                with _cwd(root):
                    load_knowledge_config()

    def test_config_rejects_empty_allowed_categories(self):
        with _tempdir() as tmpdir:
            root = tmpdir / "kb"
            config_path = write_knowledge_config(root)
            text = config_path.read_text(encoding="utf-8").replace(
                'allowed_categories = ["web_article", "note", "thought", "discussion_summary", "document", "reference", "repo", "paper", "social_post", "web", "dev", "test"]',
                "allowed_categories = []",
            )
            config_path.write_text(text, encoding="utf-8")

            with self.assertRaises(ConfigError):
                with _cwd(root):
                    load_knowledge_config()

    def test_config_file_root_defaults_to_instance_home(self):
        with _tempdir() as tmpdir:
            config_path = tmpdir / "clawsqlite.toml"
            config_path.write_text("[knowledge]\ndb = \"knowledge.sqlite3\"\n", encoding="utf-8")

            with _cwd(tmpdir):
                cfg = load_knowledge_config()

            self.assertEqual(cfg.root, str(tmpdir))
            self.assertEqual(cfg.db, str(tmpdir / "knowledge.sqlite3"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
