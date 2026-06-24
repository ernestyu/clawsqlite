# -*- coding: utf-8 -*-
"""Regression tests for optional analysis dependencies."""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_BIN = sys.executable


class OptionalAnalysisDependencyTests(unittest.TestCase):
    def test_knowledge_cli_import_does_not_require_report_interest_or_numpy(self):
        script = textwrap.dedent(
            """
            import builtins

            real_import = builtins.__import__

            def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "numpy" or name.startswith("numpy."):
                    raise ModuleNotFoundError("blocked numpy for regression test")
                if name == "clawsqlite_knowledge.report_interest":
                    raise ModuleNotFoundError("blocked report_interest for regression test")
                return real_import(name, globals, locals, fromlist, level)

            builtins.__import__ = guarded_import

            from clawsqlite_knowledge import cli

            parser = cli.build_parser()
            assert parser.prog == "clawsqlite knowledge"
            print("ok")
            """
        )
        proc = subprocess.run(
            [PYTHON_BIN, "-c", script],
            cwd=str(REPO_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            self.fail(
                f"CLI import should not require optional analysis deps\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        self.assertIn("ok", proc.stdout)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
