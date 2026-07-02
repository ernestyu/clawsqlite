# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
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
            old_xdg = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(Path(tmp) / "xdg")
            home = Path(tmp) / "kb-home"
            try:
                code, out, err = _run_cli(["maintenance", "init-config", "--home", str(home), "--json"])
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_xdg
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            config_path = home / "clawsqlite.toml"
            self.assertEqual(payload["config_path"], str(config_path.resolve()))
            self.assertEqual(payload["instance_home"], str(home.resolve()))
            self.assertEqual(Path(payload["default_instance_registry"]).read_text(encoding="utf-8").strip(), str(home.resolve()))
            self.assertTrue(config_path.is_file())
            self.assertIn('root = "."', config_path.read_text(encoding="utf-8"))

    def test_init_config_instance_uses_xdg_data_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_xdg = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(Path(tmp) / "xdg")
            try:
                code, out, err = _run_cli(["maintenance", "init-config", "--instance", "default", "--json"])
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_xdg
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            expected_home = Path(tmp) / "xdg" / "clawsqlite-knowledge" / "default"
            self.assertEqual(payload["instance_home"], str(expected_home.resolve()))
            self.assertEqual(Path(payload["default_instance_registry"]).read_text(encoding="utf-8").strip(), str(expected_home.resolve()))
            self.assertTrue((expected_home / "clawsqlite.toml").is_file())

    def test_skill_wrapper_uses_registered_default_instance_from_any_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            xdg = Path(tmp) / "xdg"
            old_xdg = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = str(xdg)
            try:
                code, _, err = _run_cli(["maintenance", "init-config", "--instance", "default", "--json"])
            finally:
                if old_xdg is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old_xdg
            self.assertEqual(code, 0, err)

            run_cwd = Path(tmp) / "elsewhere"
            run_cwd.mkdir()
            env = os.environ.copy()
            env["XDG_DATA_HOME"] = str(xdg)
            env["PYTHON"] = sys.executable
            env["PYTHONPATH"] = str(REPO_ROOT)
            wrapper = REPO_ROOT / "skills" / "clawsqlite-knowledge" / "bin" / "clawsqlite"
            proc = subprocess.run(
                [str(wrapper), "knowledge", "maintenance", "doctor", "--json"],
                cwd=str(run_cwd),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            expected_home = xdg / "clawsqlite-knowledge" / "default"
            self.assertEqual(report["active_config"]["root"], str(expected_home.resolve()))

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
