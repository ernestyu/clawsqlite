# -*- coding: utf-8 -*-
from __future__ import annotations

"""Self-check utilities for clawsqlite knowledge base.

`clawsqlite knowledge doctor` 会运行一组检查，输出一份 JSON 报告，
帮助你快速判断：

- clawsqlite.toml 中的 root / DB 是否指向有效的知识库；
- vec0 扩展和 vec 表是否可用；
- Embedding 配置是否完整且能正常调用；
- [llm] 配置是否完整；
- 当前大致处于哪个 capability mode（有/无 embedding + 有/无 small LLM）。

该命令不会修改任何数据，只做只读检查。
"""

import json
import os
import sqlite3
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .embed import embedding_enabled, get_embedding, _embedding_missing_keys, _resolve_vec_dim
from . import db as dbmod
from .config import KnowledgeConfig


@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str
    next: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


def _load_paths_from_config(config: Optional[KnowledgeConfig] = None) -> Dict[str, str]:
    """Resolve root/db/articles-dir for doctor from clawsqlite.toml."""

    if config is not None:
        return {"root": config.root, "db": config.db, "articles_dir": config.articles_dir}

    return {"root": "", "db": "", "articles_dir": ""}


def _check_kb_paths(config: Optional[KnowledgeConfig] = None) -> CheckResult:
    paths = _load_paths_from_config(config)
    root = paths["root"]
    db_path = paths["db"]

    root_exists = bool(root) and Path(root).is_dir()
    db_exists = bool(db_path) and Path(db_path).is_file()

    if not root_exists and not db_exists:
        return CheckResult(
            name="knowledge_paths",
            ok=False,
            message=(
                f"Knowledge root {root!r} and DB {db_path!r} do not exist or are not configured."
            ),
            next=(
                "Create or fix clawsqlite.toml ([knowledge].root/[knowledge].db), "
                "then run 'clawsqlite knowledge ingest' to initialize the database."
            ),
            details={"root": root, "db": db_path},
        )

    if root_exists and not db_exists:
        return CheckResult(
            name="knowledge_paths",
            ok=False,
            message=(
                f"Knowledge root exists at {root!r}, but DB file {db_path!r} is missing."
            ),
            next=(
                "Point [knowledge].db in clawsqlite.toml to an existing knowledge DB, "
                "or run a first ingest via 'clawsqlite knowledge ingest' to create it."
            ),
            details={"root": root, "db": db_path},
        )

    if not root_exists and db_exists:
        return CheckResult(
            name="knowledge_paths",
            ok=True,
            message=(
                f"DB file exists at {db_path!r}, but configured root {root!r} directory is missing."
            ),
            next=(
                "Set [knowledge].root in clawsqlite.toml to the directory that contains "
                "your articles/, or recreate the expected directory tree."
            ),
            details={"root": root, "db": db_path},
        )

    return CheckResult(
        name="knowledge_paths",
        ok=True,
        message=f"Knowledge DB found at {db_path!r} under root {root!r}",
        details={"root": root, "db": db_path},
    )


def _check_db_schema(config: Optional[KnowledgeConfig] = None) -> CheckResult:
    paths = _load_paths_from_config(config)
    db_path = paths["db"]
    if not db_path or not Path(db_path).is_file():
        return CheckResult(
            name="db_schema",
            ok=False,
            message=f"DB path {db_path!r} not found; cannot inspect schema.",
            next=(
                "Set [knowledge].db in clawsqlite.toml to an existing knowledge DB or "
                "run an ingest command to initialize it, then rerun doctor."
            ),
        )

    try:
        conn = sqlite3.connect(db_path)
    except Exception as e:  # pragma: no cover - defensive
        return CheckResult(
            name="db_schema",
            ok=False,
            message=f"Failed to open DB at {db_path!r}: {e}",
            next="Verify file permissions and DB path, then rerun doctor.",
        )

    try:
        cur = conn.cursor()
        # Minimal schema check for the core KB. Vector and interest-cluster
        # tables are optional capabilities and are reported separately.
        tables_raw = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        tables = {r[0] for r in tables_raw}

        missing = []
        for required in ("articles", "articles_fts"):
            if required not in tables:
                missing.append(required)

        if missing:
            return CheckResult(
                name="db_schema",
                ok=False,
                message=(
                    "DB is missing required tables: " + ", ".join(sorted(missing))
                ),
                next=(
                    "Ensure you're pointing to a clawsqlite knowledge DB. If this is an older "
                    "schema, run 'clawsqlite knowledge reindex --rebuild --fts' to migrate."
                ),
                details={"missing_tables": sorted(missing), "tables": sorted(tables)},
            )

        return CheckResult(
            name="db_schema",
            ok=True,
            message="Knowledge DB core schema looks consistent (articles + FTS present).",
            details={
                "tables": sorted(tables),
                "optional_tables": {
                    "articles_vec": "articles_vec" in tables,
                    "articles_tag_vec": "articles_tag_vec" in tables,
                    "interest_clusters": "interest_clusters" in tables,
                },
            },
        )
    finally:
        conn.close()


def _check_vec_extension(config: Optional[KnowledgeConfig] = None) -> CheckResult:
    paths = _load_paths_from_config(config)
    db_path = paths["db"]
    if not db_path or not Path(db_path).is_file():
        return CheckResult(
            name="vec_extension",
            ok=False,
            message=f"DB path {db_path!r} not found; cannot verify vec0 extension.",
            next="Set [knowledge].db in clawsqlite.toml to a valid knowledge DB, then rerun doctor.",
        )

    try:
        conn = dbmod.open_db(db_path, need_fts=False, need_vec=True, tokenizer_ext=None, vec_ext=None)
    except Exception as e:
        return CheckResult(
            name="vec_extension",
            ok=False,
            message=(
                f"Opening DB with vec support failed: {e}. vec0 extension may be missing or misconfigured."
            ),
            next=(
                "Ensure the sqlite-vec extension (vec0) is installed and configured. "
                "Use explicit CLI extension flags for debugging, or install vec0 where clawsqlite can auto-discover it."
            ),
        )

    try:
        if not dbmod.vec_table_exists(conn):
            return CheckResult(
                name="vec_extension",
                ok=False,
                message=(
                    "Vec tables are not available in this DB (articles_vec/articles_tag_vec missing)."
                ),
                next=(
                    "Run 'clawsqlite knowledge reindex --rebuild --vec' to create vec tables, and "
                    "ensure vec0 is available."
                ),
            )

        return CheckResult(
            name="vec_extension",
            ok=True,
            message="Vec0 extension and vec tables appear to be available.",
        )
    finally:
        conn.close()


def _embedding_missing_config(config: Optional[KnowledgeConfig] = None) -> List[str]:
    if config is None:
        return _embedding_missing_keys()
    missing: List[str] = []
    if not config.embedding.base_url:
        missing.append("[embedding].base_url")
    if not config.embedding.model:
        missing.append("[embedding].model")
    if not config.embedding.resolved_api_key:
        missing.append("[embedding].api_key")
    if config.embedding.dim <= 0:
        missing.append("[embedding].dim")
    return missing


def _check_embedding_config(config: Optional[KnowledgeConfig] = None) -> CheckResult:
    missing = _embedding_missing_config(config)
    if missing:
        return CheckResult(
            name="embedding_config",
            ok=False,
            message="Embedding config incomplete: missing " + ", ".join(sorted(missing)),
            next=(
                "Set [embedding].base_url/model/api_key/dim in clawsqlite.toml "
                "to enable hybrid/vec search and background embeddings."
            ),
            details={"missing_keys": sorted(missing)},
        )

    # If config looks present, also sanity-check vec_dim.
    try:
        vec_dim = config.embedding.dim if config is not None else _resolve_vec_dim()
    except Exception as e:
        return CheckResult(
            name="embedding_config",
            ok=False,
            message=f"Embedding vec_dim invalid: {e}",
            next="Ensure [embedding].dim is a positive integer matching your embedding model and existing vec schema.",
        )

    return CheckResult(
        name="embedding_config",
        ok=True,
        message="Embedding config in clawsqlite.toml looks complete.",
        details={"vec_dim": vec_dim},
    )


def _check_embedding_roundtrip(config: Optional[KnowledgeConfig] = None) -> CheckResult:
    # Only attempt a real call when config is present; otherwise embedding_config
    # will already be false.
    missing = _embedding_missing_config(config)
    if missing:
        return CheckResult(
            name="embedding_roundtrip",
            ok=False,
            message="Skipping embedding roundtrip test because config is incomplete.",
            next="Fix embedding_config first; then rerun doctor to verify HTTP connectivity.",
        )

    try:
        vec = get_embedding("clawsqlite doctor ping")
        n = len(vec)
        if n == 0:
            return CheckResult(
                name="embedding_roundtrip",
                ok=False,
                message="Embedding service returned an empty vector.",
                next="Check [embedding] in clawsqlite.toml and provider docs.",
            )
        return CheckResult(
            name="embedding_roundtrip",
            ok=True,
            message=f"Embedding service reachable; got vector dim={n}.",
            details={"dim": n},
        )
    except Exception as e:
        return CheckResult(
            name="embedding_roundtrip",
            ok=False,
            message=f"Embedding request failed: {e}",
            next="Verify [embedding].base_url/model/api_key in clawsqlite.toml and network reachability.",
        )


def _llm_missing_config(config: Optional[KnowledgeConfig] = None) -> List[str]:
    if config is not None:
        missing: List[str] = []
        if not config.llm.base_url:
            missing.append("[llm].base_url")
        if not config.llm.model:
            missing.append("[llm].model")
        if not config.llm.resolved_api_key:
            missing.append("[llm].api_key")
        return missing

    missing = []
    if not os.environ.get("LLM_BASE_URL"):
        missing.append("[llm].base_url")
    if not os.environ.get("LLM_MODEL"):
        missing.append("[llm].model")
    if not os.environ.get("LLM_API_KEY"):
        missing.append("[llm].api_key")
    return missing


def _check_llm_config(config: Optional[KnowledgeConfig] = None) -> CheckResult:
    missing = _llm_missing_config(config)
    base = config.llm.base_url if config is not None else os.environ.get("LLM_BASE_URL")
    model = config.llm.model if config is not None else os.environ.get("LLM_MODEL")
    key = config.llm.resolved_api_key if config is not None else os.environ.get("LLM_API_KEY")

    if len(missing) == 3:
        return CheckResult(
            name="llm_config",
            ok=False,
            message="LLM not configured ([llm].base_url/model/api_key all empty).",
            next=(
                "If you want LLM-based summaries/tags/query_refine, set "
                "[llm].base_url/model/api_key in clawsqlite.toml."
            ),
        )

    if missing:
        return CheckResult(
            name="llm_config",
            ok=False,
            message="LLM config is partially set: missing " + ", ".join(sorted(missing)),
            next="Set all of [llm].base_url, [llm].model, and [llm].api_key in clawsqlite.toml or clear them all.",
            details={
                "base_url": base,
                "model": model,
                "has_key": bool(key),
                "missing_keys": sorted(missing),
            },
        )

    return CheckResult(
        name="llm_config",
        ok=True,
        message=f"LLM configured: model={model!r}, base_url={base!r}",
        details={"base_url": base, "model": model},
    )


def _check_llm_roundtrip(config: Optional[KnowledgeConfig] = None) -> CheckResult:
    missing = _llm_missing_config(config)
    if missing:
        return CheckResult(
            name="llm_roundtrip",
            ok=False,
            message="Skipping LLM roundtrip test because config is incomplete.",
            next="Fix llm_config first; then rerun doctor --check-llm to verify HTTP connectivity.",
            details={"missing_keys": sorted(missing)},
        )

    try:
        from .generator import _call_llm_json

        obj = _call_llm_json(
            "Return STRICT JSON only: {\"ok\": true, \"service\": \"clawsqlite-doctor\"}",
            timeout=config.llm.timeout_seconds if config is not None else 30,
        )
        if obj.get("ok") is not True:
            return CheckResult(
                name="llm_roundtrip",
                ok=False,
                message="LLM service responded, but did not return the expected JSON shape.",
                next="Verify [llm].model and provider JSON-mode behavior.",
                details={"response": obj},
            )
        return CheckResult(
            name="llm_roundtrip",
            ok=True,
            message="LLM service reachable and returned valid JSON.",
            details={"response": obj},
        )
    except Exception as e:
        return CheckResult(
            name="llm_roundtrip",
            ok=False,
            message=f"LLM request failed: {e}",
            next="Verify [llm].base_url/model/api_key in clawsqlite.toml and network reachability.",
        )


def _check_capability_mode(config: Optional[KnowledgeConfig] = None) -> CheckResult:
    # This mirrors the high-level capability modes used in search scoring.
    missing = _embedding_missing_config(config)
    embedding_ok = not missing

    if config is not None:
        llm_base = config.llm.base_url
        llm_model = config.llm.model
        llm_key = config.llm.resolved_api_key
    else:
        llm_base = os.environ.get("LLM_BASE_URL") or ""
        llm_model = os.environ.get("LLM_MODEL") or ""
        llm_key = os.environ.get("LLM_API_KEY") or ""
    llm_ok = bool(llm_base and llm_model and llm_key)

    if embedding_ok and llm_ok:
        mode = 1
        desc = "Mode1: LLM + Embedding (full hybrid scoring)."
    elif llm_ok and not embedding_ok:
        mode = 2
        desc = "Mode2: LLM + no Embedding (FTS + lexical tags only)."
    elif embedding_ok and not llm_ok:
        mode = 3
        desc = "Mode3: no LLM + Embedding (vec + FTS + lexical tags)."
    else:
        mode = 4
        desc = "Mode4: no LLM + no Embedding (FTS + lexical tags only)."

    return CheckResult(
        name="capability_mode",
        ok=True,
        message=desc,
        details={
            "mode": mode,
            "embedding_ok": embedding_ok,
            "llm_ok": llm_ok,
        },
    )


def _db_summary(config: Optional[KnowledgeConfig]) -> Dict[str, Any]:
    paths = _load_paths_from_config(config)
    db_path = paths["db"]
    out: Dict[str, Any] = {
        "exists": bool(db_path and Path(db_path).is_file()),
        "article_count": 0,
        "low_quality_count": 0,
        "failed_ingest_count": 0,
    }
    if not out["exists"]:
        return out
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        out["article_count"] = int(cur.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        out["low_quality_count"] = int(
            cur.execute(
                "SELECT COUNT(*) FROM articles WHERE deleted_at IS NULL AND coalesce(generation_quality,'') != 'llm'"
            ).fetchone()[0]
        )
        out["failed_ingest_count"] = int(
            cur.execute(
                "SELECT COUNT(*) FROM articles WHERE coalesce(ingest_status,'') = 'failed'"
            ).fetchone()[0]
        )
    except Exception:
        pass
    finally:
        try:
            conn.close()  # type: ignore[name-defined]
        except Exception:
            pass
    return out


def _config_report(config: Optional[KnowledgeConfig]) -> Dict[str, Any]:
    if config is None:
        return {}
    return {
        "active_config": {
            "config_path": config.config_path,
            "config_resolution_mode": config.config_resolution_mode,
            "config_source_reason": config.config_source_reason,
            "root": config.root,
            "db": config.db,
            "articles_dir": config.articles_dir,
        },
        "ingest_policy": {
            "require_llm": config.ingest.require_llm,
            "require_embedding": config.ingest.require_embedding,
            "summary_mode": config.ingest.summary_mode,
            "summary_target_chars": config.ingest.summary_target_chars,
            "tags_mode": config.ingest.tags_mode,
            "tag_count": config.ingest.tag_count,
            "allowed_categories": list(config.ingest.allowed_categories),
            "fallback": config.ingest.fallback,
        },
        "llm": {
            "configured": bool(config.llm.base_url and config.llm.model and config.llm.resolved_api_key),
            "model": config.llm.model,
            "base_url": config.llm.base_url,
            "has_api_key": bool(config.llm.resolved_api_key),
            "context_window_chars": config.llm.context_window_chars,
            "prompt_reserved_chars": config.llm.prompt_reserved_chars,
        },
        "embedding": {
            "configured": bool(config.embedding.base_url and config.embedding.model and config.embedding.resolved_api_key and config.embedding.dim > 0),
            "model": config.embedding.model,
            "base_url": config.embedding.base_url,
            "has_api_key": bool(config.embedding.resolved_api_key),
            "dim": config.embedding.dim,
        },
        "fts": {
            "jieba": config.fts.jieba,
        },
        "search": {
            "query_tag_min": config.search.query_tag_min,
            "query_tag_max": config.search.query_tag_max,
            "weights": {
                "mode1": config.search.weights_mode1,
                "mode2": config.search.weights_mode2,
                "mode3": config.search.weights_mode3,
                "mode4": config.search.weights_mode4,
            },
            "tag_vec_fraction": config.search.tag_vec_fraction,
            "tag_fts_log_alpha": config.search.tag_fts_log_alpha,
        },
        "interest": {
            "cluster_algo": config.interest.cluster_algo,
            "tag_weight": config.interest.tag_weight,
            "use_pca": config.interest.use_pca,
            "pca_explained_variance_threshold": config.interest.pca_explained_variance_threshold,
            "min_size": config.interest.min_size,
            "max_clusters": config.interest.max_clusters,
            "kmeans_random_state": config.interest.kmeans_random_state,
            "kmeans_n_init": config.interest.kmeans_n_init,
            "kmeans_max_iter": config.interest.kmeans_max_iter,
            "enable_post_merge": config.interest.enable_post_merge,
            "merge_distance_threshold": config.interest.merge_distance_threshold,
            "hierarchical_linkage": config.interest.hierarchical_linkage,
            "hierarchical_distance_threshold": config.interest.hierarchical_distance_threshold,
            "merge_alpha": config.interest.merge_alpha,
        },
        "report": {
            "lang": config.report.lang,
        },
    }


def run_doctor(
    *,
    config: Optional[KnowledgeConfig] = None,
    check_embedding: bool = False,
    check_llm: bool = False,
) -> int:
    checks: List[CheckResult] = []

    checks.append(_check_kb_paths(config))
    checks.append(_check_db_schema(config))
    checks.append(_check_vec_extension(config))
    checks.append(_check_embedding_config(config))
    if check_embedding:
        checks.append(_check_embedding_roundtrip(config))
    checks.append(_check_llm_config(config))
    if check_llm:
        checks.append(_check_llm_roundtrip(config))
    checks.append(_check_capability_mode(config))

    any_error = any(not c.ok for c in checks if c.name in {"knowledge_paths", "db_schema"})

    report = {
        "ok": not any_error,
        "checks": [asdict(c) for c in checks],
        "db": _db_summary(config),
        "roundtrip": {
            "llm_checked": bool(check_llm),
            "embedding_checked": bool(check_embedding),
        },
    }
    report.update(_config_report(config))

    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    print()  # newline
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_doctor())
