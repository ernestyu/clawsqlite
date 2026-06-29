#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin JSON adapter for `clawsqlite knowledge`.

This script intentionally does not implement knowledge-base behavior. It only:

1. accepts a small JSON request;
2. builds a `clawsqlite knowledge ... --json` command;
3. executes the CLI;
4. returns normalized JSON for Agents.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional


Runner = Callable[..., subprocess.CompletedProcess[str]]

ALLOWED_ACTIONS = {"ingest_url", "ingest_text", "search", "show", "doctor"}
ALLOWED_FIELDS = {
    "action",
    "allow_heuristic",
    "allow_missing_embedding",
    "timeout_seconds",
    "url",
    "text",
    "title",
    "summary",
    "tags",
    "category",
    "priority",
    "update_existing",
    "gen_provider",
    "query",
    "mode",
    "topk",
    "candidates",
    "llm_keywords",
    "tag",
    "since",
    "include_deleted",
    "explain",
    "id",
    "full",
    "check_embedding",
}
FORBIDDEN_PATH_OVERRIDES = {
    "root",
    "db",
    "articles_dir",
    "articles-dir",
    "tokenizer_ext",
    "tokenizer-ext",
    "vec_ext",
    "vec-ext",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _json_fail(
    *,
    action: str,
    kind: str,
    message: str,
    next_hint: Optional[str] = None,
    exit_code: int = 2,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "action": action,
        "exit_code": exit_code,
        "data": None,
        "error": {
            "kind": kind,
            "message": message,
            "next": next_hint,
        },
    }


def _read_request(args: argparse.Namespace) -> Dict[str, Any]:
    if args.request:
        raw = args.request
    elif args.request_file:
        raw = Path(args.request_file).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    raw = raw.strip()
    if not raw:
        raise ValueError("empty JSON request")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("request must be a JSON object")
    return obj


def _append_optional(argv: List[str], request: Mapping[str, Any], field: str, flag: str) -> None:
    value = request.get(field)
    if value is not None and value != "":
        argv.extend([flag, str(value)])


def _append_ingest_common(argv: List[str], request: Mapping[str, Any]) -> None:
    for field, flag in [
        ("title", "--title"),
        ("summary", "--summary"),
        ("tags", "--tags"),
        ("category", "--category"),
        ("priority", "--priority"),
        ("gen_provider", "--gen-provider"),
    ]:
        _append_optional(argv, request, field, flag)
    if bool(request.get("update_existing")):
        argv.append("--update-existing")
    if bool(request.get("allow_heuristic")):
        argv.append("--allow-heuristic")
    if bool(request.get("allow_missing_embedding")):
        argv.append("--allow-missing-embedding")


def build_cli_args(request: Mapping[str, Any]) -> Dict[str, Any]:
    action = str(request.get("action") or "").strip()
    if action not in ALLOWED_ACTIONS:
        return _json_fail(
            action=action or "unknown",
            kind="invalid_action",
            message=f"Unsupported action: {action!r}",
            next_hint="Use one of: ingest_url, ingest_text, search, show, doctor.",
        )

    forbidden = sorted(k for k in FORBIDDEN_PATH_OVERRIDES if k in request)
    if forbidden:
        return _json_fail(
            action=action,
            kind="path_override_forbidden",
            message="This Skill does not accept path override fields: " + ", ".join(forbidden),
            next_hint="Put root/db/articles_dir in the project-root clawsqlite.toml.",
        )

    unknown = sorted(str(k) for k in request.keys() if str(k) not in ALLOWED_FIELDS)
    if unknown:
        return _json_fail(
            action=action,
            kind="invalid_input",
            message="Unknown request fields: " + ", ".join(unknown),
            next_hint="Use the documented JSON fields in schema.json.",
        )

    argv = [sys.executable, "-m", "clawsqlite_cli", "knowledge"]

    if action == "ingest_url":
        url = str(request.get("url") or "").strip()
        if not url:
            return _json_fail(action=action, kind="invalid_input", message="ingest_url requires url.")
        argv.extend(["ingest", "--url", url, "--json"])
        _append_ingest_common(argv, request)
        return {"ok": True, "action": action, "argv": argv}

    if action == "ingest_text":
        text = str(request.get("text") or "").strip()
        if not text:
            return _json_fail(action=action, kind="invalid_input", message="ingest_text requires text.")
        argv.extend(["ingest", "--text", text, "--json"])
        _append_ingest_common(argv, request)
        return {"ok": True, "action": action, "argv": argv}

    if action == "search":
        query = str(request.get("query") or "").strip()
        if not query:
            return _json_fail(action=action, kind="invalid_input", message="search requires query.")
        argv.extend(["search", query, "--json"])
        for field, flag in [
            ("mode", "--mode"),
            ("topk", "--topk"),
            ("candidates", "--candidates"),
            ("llm_keywords", "--llm-keywords"),
            ("gen_provider", "--gen-provider"),
            ("category", "--category"),
            ("tag", "--tag"),
            ("since", "--since"),
            ("priority", "--priority"),
        ]:
            _append_optional(argv, request, field, flag)
        if bool(request.get("include_deleted")):
            argv.append("--include-deleted")
        if bool(request.get("explain")):
            argv.append("--explain")
        return {"ok": True, "action": action, "argv": argv}

    if action == "show":
        article_id = request.get("id")
        if article_id is None or str(article_id).strip() == "":
            return _json_fail(action=action, kind="invalid_input", message="show requires id.")
        argv.extend(["show", "--id", str(article_id), "--json"])
        if bool(request.get("full")):
            argv.append("--full")
        return {"ok": True, "action": action, "argv": argv}

    argv.extend(["doctor", "--json"])
    if bool(request.get("check_embedding")):
        argv.append("--check-embedding")
    return {"ok": True, "action": action, "argv": argv}


def _parse_stderr(stderr: str) -> Dict[str, Any]:
    kind: Optional[str] = None
    errors: List[str] = []
    next_lines: List[str] = []
    warnings: List[str] = []
    for raw_line in (stderr or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("ERROR_KIND:"):
            kind = line.split(":", 1)[1].strip() or kind
        elif line.startswith("ERROR:"):
            errors.append(line.split(":", 1)[1].strip())
        elif line.startswith("NEXT:"):
            next_lines.append(line.split(":", 1)[1].strip())
        elif line.startswith("WARNING:"):
            warnings.append(line.split(":", 1)[1].strip())
    return {
        "kind": kind,
        "message": "\n".join(errors) if errors else (stderr or "").strip(),
        "next": "\n".join(next_lines) if next_lines else None,
        "warnings": warnings,
    }


def _parse_stdout(stdout: str) -> Any:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _run_cli(argv: List[str], *, timeout: int, runner: Runner) -> subprocess.CompletedProcess[str]:
    repo_root = _repo_root()
    env = os.environ.copy()
    if (repo_root / "clawsqlite_cli.py").exists():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(repo_root) if not existing else str(repo_root) + os.pathsep + existing
    return runner(
        argv,
        cwd=str(repo_root),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def handle_request(request: Mapping[str, Any], *, runner: Runner = subprocess.run) -> Dict[str, Any]:
    built = build_cli_args(request)
    if not built.get("ok"):
        return built
    action = str(built["action"])
    argv = list(built["argv"])
    timeout = int(request.get("timeout_seconds") or 600)
    try:
        proc = _run_cli(argv, timeout=timeout, runner=runner)
    except subprocess.TimeoutExpired as e:
        return _json_fail(
            action=action,
            kind="timeout",
            message=f"clawsqlite knowledge command timed out after {timeout} seconds.",
            next_hint="Check scraper/LLM/embedding endpoints, then retry with a higher timeout_seconds if appropriate.",
            exit_code=124,
        ) | {"diagnostics": {"command": argv, "stdout": str(e.stdout or ""), "stderr": str(e.stderr or "")}}
    except Exception as e:
        return _json_fail(
            action=action,
            kind="adapter_error",
            message=str(e),
            next_hint="Inspect the adapter environment and ensure clawsqlite is importable.",
            exit_code=4,
        ) | {"diagnostics": {"command": argv}}

    data = _parse_stdout(proc.stdout)
    parsed_err = _parse_stderr(proc.stderr)
    diagnostics = {
        "command": argv,
        "stderr": (proc.stderr or "").strip(),
        "warnings": parsed_err.get("warnings") or [],
    }
    if proc.returncode == 0:
        return {
            "ok": True,
            "action": action,
            "exit_code": 0,
            "data": data,
            "error": None,
            "diagnostics": diagnostics,
        }

    return {
        "ok": False,
        "action": action,
        "exit_code": proc.returncode,
        "data": data,
        "error": {
            "kind": parsed_err.get("kind") or "clawsqlite_error",
            "message": parsed_err.get("message") or f"clawsqlite exited with code {proc.returncode}",
            "next": parsed_err.get("next"),
        },
        "diagnostics": diagnostics,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Thin JSON adapter for clawsqlite knowledge.")
    parser.add_argument("--request", default=None, help="JSON request object as a string")
    parser.add_argument("--request-file", default=None, help="Path to a JSON request file")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    try:
        request = _read_request(args)
        result = handle_request(request)
    except Exception as e:
        result = _json_fail(
            action="unknown",
            kind="invalid_request",
            message=str(e),
            next_hint="Pass a valid JSON object on stdin, --request, or --request-file.",
        )

    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None) + "\n")
    return 0 if result.get("ok") else int(result.get("exit_code") or 1)


if __name__ == "__main__":
    raise SystemExit(main())
