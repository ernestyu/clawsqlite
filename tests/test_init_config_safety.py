# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from clawsqlite_knowledge import cli as kcli


REPO_ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def _cwd(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _run_cli(argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = kcli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


class InitConfigSafetyTests(unittest.TestCase):
    def test_init_config_refuses_source_repo_target(self):
        unsafe = REPO_ROOT / ".tmp_tests" / f"unsafe_init_{uuid.uuid4().hex}" / "clawsqlite.toml"
        shutil.rmtree(unsafe.parent, ignore_errors=True)
        with _cwd(REPO_ROOT):
            code, _, err = _run_cli(["maintenance", "init-config", "--out", str(unsafe)])
        self.assertEqual(code, 2)
        self.assertIn("ERROR_KIND: unsafe_instance_home", err)
        self.assertIn("source repository", err)
        self.assertIn("init-config --instance default", err)
        self.assertFalse(unsafe.exists())
        self.assertFalse(unsafe.parent.exists())

    def test_init_config_home_creates_config_in_explicit_instance_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "kb-home"
            code, out, err = _run_cli(["maintenance", "init-config", "--home", str(home), "--json"])
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            config_path = home / "clawsqlite.toml"
            self.assertEqual(payload["config_path"], str(config_path.resolve()))
            self.assertEqual(payload["instance_home"], str(home.resolve()))
            self.assertTrue(config_path.is_file())
            self.assertIn('root = "."', config_path.read_text(encoding="utf-8"))

    def test_init_config_instance_uses_openclaw_data_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = tmp
            try:
                code, out, err = _run_cli(["maintenance", "init-config", "--instance", "default", "--json"])
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            expected_home = Path(tmp) / ".openclaw" / "workspace" / "data" / "clawsqlite-knowledge" / "default"
            self.assertEqual(payload["instance_home"], str(expected_home.resolve()))
            self.assertTrue((expected_home / "clawsqlite.toml").is_file())

    def test_init_config_rejects_ambiguous_target_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = _run_cli(
                [
                    "maintenance",
                    "init-config",
                    "--home",
                    str(Path(tmp) / "kb-home"),
                    "--out",
                    "clawsqlite.toml",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("ERROR_KIND: invalid_init_config_target", err)
        self.assertIn("Use only one of --out, --home, or --instance", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
