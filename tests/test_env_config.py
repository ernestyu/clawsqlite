# -*- coding: utf-8 -*-
"""Tests for env/config resolution behaviors."""
from __future__ import annotations

import os
import contextlib
import shutil
import unittest
import uuid
from pathlib import Path

from clawsqlite_knowledge import db as dbmod
from clawsqlite_knowledge import embed as embedmod
from clawsqlite_knowledge.utils import resolve_root_paths


class EnvConfigTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        cls._base_tmp = Path(os.environ.get("CLAWSQLITE_TEST_TMP", str(repo_root / ".tmp_tests")))
        cls._base_tmp.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def _tempdir(self):
        path = self._base_tmp / f"tmp_{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=False)
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_root_defaults_to_cwd_knowledge_data(self):
        with self._tempdir() as tmpdir:
            cwd = Path(tmpdir)
            old = Path.cwd()
            os.chdir(cwd)
            try:
                paths = resolve_root_paths()
                self.assertEqual(paths["root"], str(cwd / "knowledge_data"))
            finally:
                os.chdir(old)

    def test_root_env_overrides_default(self):
        with self._tempdir() as tmpdir:
            env_root = str(Path(tmpdir) / "env_root")
            default_root = str(Path(tmpdir) / "default_root")
            os.environ["CLAWSQLITE_ROOT"] = env_root
            paths = resolve_root_paths(default_root=default_root)
            self.assertEqual(paths["root"], env_root)

    def test_root_cli_overrides_env(self):
        with self._tempdir() as tmpdir:
            env_root = str(Path(tmpdir) / "env_root")
            cli_root = str(Path(tmpdir) / "cli_root")
            os.environ["CLAWSQLITE_ROOT"] = env_root
            paths = resolve_root_paths(cli_root=cli_root)
            self.assertEqual(paths["root"], cli_root)

    def test_embedding_missing_keys_flags_invalid_dim(self):
        os.environ["EMBEDDING_MODEL"] = "m"
        os.environ["EMBEDDING_BASE_URL"] = "http://example.invalid"
        os.environ["EMBEDDING_API_KEY"] = "k"
        os.environ["CLAWSQLITE_VEC_DIM"] = "not-an-int"
        missing = embedmod._embedding_missing_keys()
        self.assertIn("CLAWSQLITE_VEC_DIM", missing)
        self.assertFalse(embedmod.embedding_enabled())

    def test_resolve_vec_dim_requires_valid_value(self):
        os.environ["CLAWSQLITE_VEC_DIM"] = "1024"
        self.assertEqual(embedmod._resolve_vec_dim(), 1024)
        os.environ["CLAWSQLITE_VEC_DIM"] = "0"
        with self.assertRaises(ValueError):
            embedmod._resolve_vec_dim()

    def test_vec_schema_none_without_dim(self):
        os.environ.pop("CLAWSQLITE_VEC_DIM", None)
        self.assertIsNone(dbmod._vec_schema())

    def test_vec_schema_uses_configured_dim(self):
        os.environ["CLAWSQLITE_VEC_DIM"] = "128"
        schema = dbmod._vec_schema()
        self.assertIsNotNone(schema)
        self.assertIn("float[128]", schema)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
