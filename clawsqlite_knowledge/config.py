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


@dataclass(frozen=True)
class IngestPolicy:
    require_llm: bool = True
    require_embedding: bool = True
    summary_mode: str = "llm"
    tags_mode: str = "llm"
    fallback: str = "fail"
    summary_target_chars: int = 800


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    api_key_env: str = ""
    timeout_seconds: int = 90
    context_window_chars: int = 24000
    prompt_reserved_chars: int = 4000
    chunk_overlap_chars: int = 500

    @property
    def content_budget_chars(self) -> int:
        return max(1000, self.context_window_chars - self.prompt_reserved_chars)

    @property
    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""

    @property
    def api_key_source(self) -> str:
        if self.api_key:
            return "config"
        if self.api_key_env and os.environ.get(self.api_key_env):
            return "env_legacy"
        if self.api_key_env:
            return "env_legacy_missing"
        return "missing"


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    api_key_env: str = ""
    dim: int = 0
    timeout_seconds: int = 300
    content: str = "summary"

    @property
    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""

    @property
    def api_key_source(self) -> str:
        if self.api_key:
            return "config"
        if self.api_key_env and os.environ.get(self.api_key_env):
            return "env_legacy"
        if self.api_key_env:
            return "env_legacy_missing"
        return "missing"


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
    env_path = os.environ.get("CLAWSQLITE_CONFIG")
    if env_path:
        return Path(env_path).expanduser()

    cur = (start or Path.cwd()).resolve()
    if cur.is_file():
        cur = cur.parent
    for path in [cur, *cur.parents]:
        candidate = path / CONFIG_FILENAME
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
    cli_config: Optional[str] = None,
    cli_root: Optional[str] = None,
    cli_db: Optional[str] = None,
    cli_articles_dir: Optional[str] = None,
    require: bool = True,
) -> KnowledgeConfig:
    cfg_path = Path(cli_config).expanduser() if cli_config else find_config_path()
    if cfg_path is None:
        if require:
            raise ConfigError(
                "clawsqlite.toml not found. Run from a project directory, pass --config, "
                "or set CLAWSQLITE_CONFIG."
            )
        root = Path(cli_root or os.environ.get("CLAWSQLITE_ROOT") or (Path.cwd() / "knowledge_data")).expanduser()
        if not root.is_absolute():
            root = root.resolve()
        db = Path(cli_db).expanduser() if cli_db else root / "knowledge.sqlite3"
        articles_dir = Path(cli_articles_dir).expanduser() if cli_articles_dir else root / "articles"
        return KnowledgeConfig(
            config_path="",
            root=str(root),
            db=str(db if db.is_absolute() else root / db),
            articles_dir=str(articles_dir if articles_dir.is_absolute() else root / articles_dir),
            ingest=IngestPolicy(),
            llm=LLMConfig(),
            embedding=EmbeddingConfig(),
            scraper=ScraperConfig(),
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

    root_text = str(cli_root or knowledge.get("root") or "").strip()
    if not root_text:
        raise ConfigError("[knowledge].root is required in clawsqlite.toml")
    root = Path(root_text).expanduser()
    if not root.is_absolute():
        root = (cfg_path.parent / root).resolve()

    db_path = _resolve_under_root(root, str(cli_db or knowledge.get("db") or ""), "knowledge.sqlite3")
    articles_dir = _resolve_under_root(root, str(cli_articles_dir or knowledge.get("articles_dir") or ""), "articles")

    fallback = _choice_value(ingest.get("fallback"), "fail", {"fail"}, name="[ingest].fallback")
    policy = IngestPolicy(
        require_llm=_bool_value(ingest.get("require_llm"), True),
        require_embedding=_bool_value(ingest.get("require_embedding"), True),
        summary_mode=_choice_value(ingest.get("summary_mode"), "llm", {"llm", "openclaw", "off"}, name="[ingest].summary_mode"),
        tags_mode=_choice_value(ingest.get("tags_mode"), "llm", {"llm", "openclaw", "off"}, name="[ingest].tags_mode"),
        fallback=fallback,
        summary_target_chars=_int_value(ingest.get("summary_target_chars"), 800, lo=100),
    )

    llm_cfg = LLMConfig(
        base_url=str(llm.get("base_url") or "").strip(),
        model=str(llm.get("model") or "").strip(),
        api_key=str(llm.get("api_key") or "").strip(),
        api_key_env=str(llm.get("api_key_env") or "").strip(),
        timeout_seconds=_int_value(llm.get("timeout_seconds"), 90, lo=1),
        context_window_chars=_int_value(llm.get("context_window_chars") or llm.get("context_chars"), 24000, lo=2000),
        prompt_reserved_chars=_int_value(llm.get("prompt_reserved_chars"), 4000, lo=500),
        chunk_overlap_chars=_int_value(llm.get("chunk_overlap_chars"), 500, lo=0),
    )

    emb_cfg = EmbeddingConfig(
        base_url=str(embedding.get("base_url") or "").strip(),
        model=str(embedding.get("model") or "").strip(),
        api_key=str(embedding.get("api_key") or "").strip(),
        api_key_env=str(embedding.get("api_key_env") or "").strip(),
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
    """Expose knowledge config through existing env-based clients."""

    os.environ["CLAWSQLITE_ROOT"] = config.root
    os.environ["CLAWSQLITE_DB"] = config.db
    os.environ["CLAWSQLITE_ARTICLES_DIR"] = config.articles_dir

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
