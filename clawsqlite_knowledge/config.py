# -*- coding: utf-8 -*-
"""Knowledge-app configuration for clawsqlite.

This module is intentionally owned by the knowledge component. The top-level
`clawsqlite admin ...` namespace also reads this config so maintenance commands
share the same component root, DB path, and runtime settings as knowledge.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
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


def _float_value(value: Any, default: float, *, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    try:
        out = float(value)
    except Exception:
        out = default
    if lo is not None and out < lo:
        out = lo
    if hi is not None and out > hi:
        out = hi
    return out


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


def _weights_value(value: Any, default: Dict[str, float], *, name: str) -> Dict[str, float]:
    if value is None:
        return dict(default)
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a TOML table")
    out: Dict[str, float] = {}
    for key in default:
        if key not in value:
            raise ConfigError(f"{name} missing weight: {key}")
        out[key] = _float_value(value.get(key), default[key], lo=0.0)
    total = sum(out.values())
    if total <= 0:
        raise ConfigError(f"{name} weights must sum to a positive value")
    return out


def _format_weights(weights: Dict[str, float]) -> str:
    return ",".join(f"{key}={float(value):g}" for key, value in weights.items())


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
class FTSConfig:
    jieba: str = "auto"


@dataclass(frozen=True)
class SearchConfig:
    query_tag_min: int = 8
    query_tag_max: int = 12
    weights_mode1: Dict[str, float] = field(default_factory=lambda: {"vec": 0.45, "fts": 0.25, "tag": 0.15, "priority": 0.03, "recency": 0.02})
    weights_mode2: Dict[str, float] = field(default_factory=lambda: {"fts": 0.60, "tag": 0.25, "priority": 0.08, "recency": 0.07})
    weights_mode3: Dict[str, float] = field(default_factory=lambda: {"vec": 0.45, "fts": 0.25, "tag": 0.15, "priority": 0.03, "recency": 0.02})
    weights_mode4: Dict[str, float] = field(default_factory=lambda: {"fts": 0.60, "tag": 0.25, "priority": 0.08, "recency": 0.07})
    tag_vec_fraction: float = 0.70
    tag_fts_log_alpha: float = 5.0


@dataclass(frozen=True)
class InterestConfig:
    cluster_algo: str = "kmeans++"
    tag_weight: float = 0.75
    use_pca: bool = True
    pca_explained_variance_threshold: float = 0.95
    min_size: int = 8
    max_clusters: int = 50
    kmeans_random_state: int = 42
    kmeans_n_init: int = 10
    kmeans_max_iter: int = 300
    enable_post_merge: bool = True
    merge_distance_threshold: float = 0.06
    hierarchical_linkage: str = "average"
    hierarchical_distance_threshold: float = 0.20
    merge_alpha: float = 0.40


@dataclass(frozen=True)
class ReportConfig:
    lang: str = "en"


@dataclass(frozen=True)
class KnowledgeConfig:
    config_path: str
    config_resolution_mode: str
    config_source_reason: str
    root: str
    db: str
    articles_dir: str
    ingest: IngestPolicy
    llm: LLMConfig
    embedding: EmbeddingConfig
    scraper: ScraperConfig
    fts: FTSConfig
    search: SearchConfig
    interest: InterestConfig
    report: ReportConfig


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
    fts = data.get("fts") or {}
    search = data.get("search") or {}
    interest = data.get("interest") or {}
    report = data.get("report") or {}

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

    fts_cfg = FTSConfig(
        jieba=_choice_value(fts.get("jieba"), "auto", {"auto", "on", "off"}, name="[fts].jieba"),
    )

    search_query = search.get("query") or {}
    search_tag = search.get("tag") or {}
    search_weights = search.get("weights") or {}
    embed_defaults = {"vec": 0.45, "fts": 0.25, "tag": 0.15, "priority": 0.03, "recency": 0.02}
    text_defaults = {"fts": 0.60, "tag": 0.25, "priority": 0.08, "recency": 0.07}
    search_cfg = SearchConfig(
        query_tag_min=_int_value(search_query.get("tag_min"), 8, lo=1),
        query_tag_max=_int_value(search_query.get("tag_max"), 12, lo=1),
        weights_mode1=_weights_value(search_weights.get("mode1"), embed_defaults, name="[search.weights.mode1]"),
        weights_mode2=_weights_value(search_weights.get("mode2"), text_defaults, name="[search.weights.mode2]"),
        weights_mode3=_weights_value(search_weights.get("mode3"), embed_defaults, name="[search.weights.mode3]"),
        weights_mode4=_weights_value(search_weights.get("mode4"), text_defaults, name="[search.weights.mode4]"),
        tag_vec_fraction=_float_value(search_tag.get("vec_fraction"), 0.70, lo=0.0, hi=1.0),
        tag_fts_log_alpha=_float_value(search_tag.get("fts_log_alpha"), 5.0, lo=0.0),
    )

    interest_cfg = InterestConfig(
        cluster_algo=_choice_value(interest.get("cluster_algo"), "kmeans++", {"kmeans++", "hierarchical"}, name="[interest].cluster_algo"),
        tag_weight=_float_value(interest.get("tag_weight"), 0.75, lo=0.0, hi=1.0),
        use_pca=_bool_value(interest.get("use_pca"), True),
        pca_explained_variance_threshold=_float_value(interest.get("pca_explained_variance_threshold"), 0.95, lo=0.0, hi=1.0),
        min_size=_int_value(interest.get("min_size"), 8, lo=1),
        max_clusters=_int_value(interest.get("max_clusters"), 50, lo=1),
        kmeans_random_state=_int_value(interest.get("kmeans_random_state"), 42, lo=0),
        kmeans_n_init=_int_value(interest.get("kmeans_n_init"), 10, lo=1),
        kmeans_max_iter=_int_value(interest.get("kmeans_max_iter"), 300, lo=1),
        enable_post_merge=_bool_value(interest.get("enable_post_merge"), True),
        merge_distance_threshold=_float_value(interest.get("merge_distance_threshold"), 0.06, lo=0.0),
        hierarchical_linkage=_choice_value(interest.get("hierarchical_linkage"), "average", {"average", "complete"}, name="[interest].hierarchical_linkage"),
        hierarchical_distance_threshold=_float_value(interest.get("hierarchical_distance_threshold"), 0.20, lo=0.0),
        merge_alpha=_float_value(interest.get("merge_alpha"), 0.40, lo=0.0),
    )

    report_cfg = ReportConfig(
        lang=_choice_value(report.get("lang"), "en", {"en", "zh"}, name="[report].lang"),
    )

    return KnowledgeConfig(
        config_path=str(cfg_path.resolve()),
        config_resolution_mode="component_root_config",
        config_source_reason=f"loaded {CONFIG_FILENAME} from the current component root",
        root=str(root),
        db=db_path,
        articles_dir=articles_dir,
        ingest=policy,
        llm=llm_cfg,
        embedding=emb_cfg,
        scraper=scraper_cfg,
        fts=fts_cfg,
        search=search_cfg,
        interest=interest_cfg,
        report=report_cfg,
    )


def apply_config_env(config: KnowledgeConfig) -> None:
    """Expose TOML service config to low-level HTTP clients."""

    if config.llm.base_url:
        os.environ["LLM_BASE_URL"] = config.llm.base_url
    else:
        os.environ.pop("LLM_BASE_URL", None)
    if config.llm.model:
        os.environ["LLM_MODEL"] = config.llm.model
    else:
        os.environ.pop("LLM_MODEL", None)
    llm_api_key = config.llm.resolved_api_key
    if llm_api_key:
        os.environ["LLM_API_KEY"] = llm_api_key
    else:
        os.environ.pop("LLM_API_KEY", None)

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
    vec_ext = os.environ.get("CLAWSQLITE_VEC_EXT") or ""
    if not vec_ext:
        try:
            from .db import _find_vec0_so

            vec_ext = _find_vec0_so() or ""
        except Exception:
            vec_ext = ""
    if vec_ext:
        os.environ["CLAWSQLITE_VEC_EXT"] = vec_ext
    else:
        os.environ.pop("CLAWSQLITE_VEC_EXT", None)
    embedding_api_key = config.embedding.resolved_api_key
    if embedding_api_key:
        os.environ["EMBEDDING_API_KEY"] = embedding_api_key
    else:
        os.environ.pop("EMBEDDING_API_KEY", None)

    if config.scraper.cmd:
        os.environ["CLAWSQLITE_SCRAPE_CMD"] = config.scraper.cmd
    else:
        os.environ.pop("CLAWSQLITE_SCRAPE_CMD", None)

    os.environ["CLAWSQLITE_FTS_JIEBA"] = config.fts.jieba
    os.environ["CLAWSQLITE_SEARCH_QUERY_TAG_MIN"] = str(config.search.query_tag_min)
    os.environ["CLAWSQLITE_SEARCH_QUERY_TAG_MAX"] = str(config.search.query_tag_max)
    os.environ["CLAWSQLITE_SCORE_WEIGHTS_MODE1"] = _format_weights(config.search.weights_mode1)
    os.environ["CLAWSQLITE_SCORE_WEIGHTS_MODE2"] = _format_weights(config.search.weights_mode2)
    os.environ["CLAWSQLITE_SCORE_WEIGHTS_MODE3"] = _format_weights(config.search.weights_mode3)
    os.environ["CLAWSQLITE_SCORE_WEIGHTS_MODE4"] = _format_weights(config.search.weights_mode4)
    os.environ["CLAWSQLITE_TAG_VEC_FRACTION"] = str(config.search.tag_vec_fraction)
    os.environ["CLAWSQLITE_TAG_FTS_LOG_ALPHA"] = str(config.search.tag_fts_log_alpha)

    os.environ["CLAWSQLITE_INTEREST_CLUSTER_ALGO"] = config.interest.cluster_algo
    os.environ["CLAWSQLITE_INTEREST_TAG_WEIGHT"] = str(config.interest.tag_weight)
    os.environ["CLAWSQLITE_INTEREST_USE_PCA"] = "true" if config.interest.use_pca else "false"
    os.environ["CLAWSQLITE_INTEREST_PCA_EXPLAINED_VARIANCE_THRESHOLD"] = str(config.interest.pca_explained_variance_threshold)
    os.environ["CLAWSQLITE_INTEREST_MIN_SIZE"] = str(config.interest.min_size)
    os.environ["CLAWSQLITE_INTEREST_MAX_CLUSTERS"] = str(config.interest.max_clusters)
    os.environ["CLAWSQLITE_INTEREST_KMEANS_RANDOM_STATE"] = str(config.interest.kmeans_random_state)
    os.environ["CLAWSQLITE_INTEREST_KMEANS_N_INIT"] = str(config.interest.kmeans_n_init)
    os.environ["CLAWSQLITE_INTEREST_KMEANS_MAX_ITER"] = str(config.interest.kmeans_max_iter)
    os.environ["CLAWSQLITE_INTEREST_ENABLE_POST_MERGE"] = "true" if config.interest.enable_post_merge else "false"
    os.environ["CLAWSQLITE_INTEREST_MERGE_DISTANCE"] = str(config.interest.merge_distance_threshold)
    os.environ["CLAWSQLITE_INTEREST_HIERARCHICAL_LINKAGE"] = config.interest.hierarchical_linkage
    os.environ["CLAWSQLITE_INTEREST_HIERARCHICAL_DISTANCE_THRESHOLD"] = str(config.interest.hierarchical_distance_threshold)
    os.environ["CLAWSQLITE_INTEREST_MERGE_ALPHA"] = str(config.interest.merge_alpha)

    os.environ["CLAWSQLITE_REPORT_LANG"] = config.report.lang
