# -*- coding: utf-8 -*-
"""Knowledge-app configuration for clawsqlite.

This module is intentionally owned by `clawsqlite_knowledge`, not plumbing.
Plumbing commands stay generic and do not read clawsqlite.toml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:  # Python 3.11+
    import tomllib  # type: ignore
except Exception:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore


CONFIG_FILENAME = "clawsqlite.toml"
DEFAULT_SUMMARY_TARGET_CHARS = 3600
DEFAULT_TAG_COUNT = 8
DEFAULT_ALLOWED_CATEGORIES = (
    "web_article",
    "note",
    "thought",
    "discussion_summary",
    "document",
    "reference",
    "repo",
    "paper",
    "social_post",
)


class ConfigError(RuntimeError):
    """Raised when knowledge config is missing or invalid."""


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "on", "y"}:
        return True
    if s in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _int_value(value: Any, default: int, *, lo: int = 1) -> int:
    try:
        out = int(value)
    except Exception:
        out = default
    return max(lo, out)


def _choice_value(value: Any, default: str, allowed: set[str], *, name: str) -> str:
    out = str(value or default).strip().lower()
    if out not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ConfigError(f"{name} must be one of: {allowed_text}")
    return out


def _string_list_value(value: Any, default: tuple[str, ...], *, name: str) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a TOML array of strings")
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        s = str(item or "").strip().lower()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    if not out:
        raise ConfigError(f"{name} must contain at least one category")
    return tuple(out)


@dataclass(frozen=True)
class IngestPolicy:
    require_llm: bool = True
    require_embedding: bool = True
    summary_mode: str = "llm"
    tags_mode: str = "llm"
    fallback: str = "fail"
    summary_target_chars: int = DEFAULT_SUMMARY_TARGET_CHARS
    tag_count: int = DEFAULT_TAG_COUNT
    allowed_categories: tuple[str, ...] = DEFAULT_ALLOWED_CATEGORIES


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: int = 90
    context_window_chars: int = 24000
    prompt_reserved_chars: int = 4000
    chunk_overlap_chars: int = 500

    @property
    def content_budget_chars(self) -> int:
        return max(1000, self.context_window_chars - self.prompt_reserved_chars)

    @property
    def resolved_api_key(self) -> str:
        return self.api_key


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    dim: int = 0
    timeout_seconds: int = 300
    content: str = "summary"

    @property
    def resolved_api_key(self) -> str:
        return self.api_key


@dataclass(frozen=True)
class ScraperConfig:
    cmd: str = ""


@dataclass(frozen=True)
class KnowledgeConfig:
    config_path: str
    root: str
    db: str
    articles_dir: str
    ingest: IngestPolicy
    llm: LLMConfig
    embedding: EmbeddingConfig
    scraper: ScraperConfig


def find_config_path(start: Optional[Path] = None) -> Optional[Path]:
    cur = (start or Path.cwd()).resolve()
    if cur.is_file():
        cur = cur.parent
    candidate = cur / CONFIG_FILENAME
    if candidate.exists():
        return candidate
    return None


def _resolve_under_root(root: Path, value: str, default_name: str) -> str:
    raw = value or default_name
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = root / p
    return str(p)


def load_knowledge_config(
    *,
    require: bool = True,
) -> KnowledgeConfig:
    cfg_path = find_config_path()
    if cfg_path is None:
        raise ConfigError(
            "clawsqlite.toml not found in the current component root. "
            "Run from the directory that contains clawsqlite.toml, or create one with init-config."
        )

    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {cfg_path}") from e
    except Exception as e:
        raise ConfigError(f"failed to read config {cfg_path}: {e}") from e

    knowledge = data.get("knowledge") or {}
    ingest = data.get("ingest") or {}
    llm = data.get("llm") or {}
    embedding = data.get("embedding") or {}
    scraper = data.get("scraper") or {}

    root_text = str(knowledge.get("root") or "").strip()
    if not root_text:
        raise ConfigError("[knowledge].root is required in clawsqlite.toml")
    root = Path(root_text).expanduser()
    if not root.is_absolute():
        root = (cfg_path.parent / root).resolve()

    db_path = _resolve_under_root(root, str(knowledge.get("db") or ""), "knowledge.sqlite3")
    articles_dir = _resolve_under_root(root, str(knowledge.get("articles_dir") or ""), "articles")

    fallback = _choice_value(ingest.get("fallback"), "fail", {"fail"}, name="[ingest].fallback")
    policy = IngestPolicy(
        require_llm=_bool_value(ingest.get("require_llm"), True),
        require_embedding=_bool_value(ingest.get("require_embedding"), True),
        summary_mode=_choice_value(ingest.get("summary_mode"), "llm", {"llm", "openclaw", "off"}, name="[ingest].summary_mode"),
        tags_mode=_choice_value(ingest.get("tags_mode"), "llm", {"llm", "openclaw", "off"}, name="[ingest].tags_mode"),
        fallback=fallback,
        summary_target_chars=_int_value(ingest.get("summary_target_chars"), DEFAULT_SUMMARY_TARGET_CHARS, lo=100),
        tag_count=_int_value(ingest.get("tag_count"), DEFAULT_TAG_COUNT, lo=1),
        allowed_categories=_string_list_value(
            ingest.get("allowed_categories"),
            DEFAULT_ALLOWED_CATEGORIES,
            name="[ingest].allowed_categories",
        ),
    )

    llm_cfg = LLMConfig(
        base_url=str(llm.get("base_url") or "").strip(),
        model=str(llm.get("model") or "").strip(),
        api_key=str(llm.get("api_key") or "").strip(),
        timeout_seconds=_int_value(llm.get("timeout_seconds"), 90, lo=1),
        context_window_chars=_int_value(llm.get("context_window_chars") or llm.get("context_chars"), 24000, lo=2000),
        prompt_reserved_chars=_int_value(llm.get("prompt_reserved_chars"), 4000, lo=500),
        chunk_overlap_chars=_int_value(llm.get("chunk_overlap_chars"), 500, lo=0),
    )

    emb_cfg = EmbeddingConfig(
        base_url=str(embedding.get("base_url") or "").strip(),
        model=str(embedding.get("model") or "").strip(),
        api_key=str(embedding.get("api_key") or "").strip(),
        dim=_int_value(embedding.get("dim"), 0, lo=0),
        timeout_seconds=_int_value(embedding.get("timeout_seconds"), 300, lo=1),
        content=str(embedding.get("content") or "summary").strip().lower(),
    )

    scraper_cfg = ScraperConfig(cmd=str(scraper.get("cmd") or "").strip())

    return KnowledgeConfig(
        config_path=str(cfg_path.resolve()),
        root=str(root),
        db=db_path,
        articles_dir=articles_dir,
        ingest=policy,
        llm=llm_cfg,
        embedding=emb_cfg,
        scraper=scraper_cfg,
    )


def apply_config_env(config: KnowledgeConfig) -> None:
    """Expose service config to existing env-based HTTP clients."""

    if config.llm.base_url:
        os.environ["SMALL_LLM_BASE_URL"] = config.llm.base_url
    else:
        os.environ.pop("SMALL_LLM_BASE_URL", None)
    if config.llm.model:
        os.environ["SMALL_LLM_MODEL"] = config.llm.model
    else:
        os.environ.pop("SMALL_LLM_MODEL", None)
    llm_api_key = config.llm.resolved_api_key
    if llm_api_key:
        os.environ["SMALL_LLM_API_KEY"] = llm_api_key
    else:
        os.environ.pop("SMALL_LLM_API_KEY", None)

    if config.embedding.base_url:
        os.environ["EMBEDDING_BASE_URL"] = config.embedding.base_url
    else:
        os.environ.pop("EMBEDDING_BASE_URL", None)
    if config.embedding.model:
        os.environ["EMBEDDING_MODEL"] = config.embedding.model
    else:
        os.environ.pop("EMBEDDING_MODEL", None)
    if config.embedding.dim > 0:
        os.environ["CLAWSQLITE_VEC_DIM"] = str(config.embedding.dim)
    else:
        os.environ.pop("CLAWSQLITE_VEC_DIM", None)
    embedding_api_key = config.embedding.resolved_api_key
    if embedding_api_key:
        os.environ["EMBEDDING_API_KEY"] = embedding_api_key
    else:
        os.environ.pop("EMBEDDING_API_KEY", None)

    if config.scraper.cmd:
        os.environ["CLAWSQLITE_SCRAPE_CMD"] = config.scraper.cmd
    else:
        os.environ.pop("CLAWSQLITE_SCRAPE_CMD", None)
