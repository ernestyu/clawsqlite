# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def write_knowledge_config(
    root: Path,
    *,
    require_llm: bool = False,
    require_embedding: bool = False,
    summary_target_chars: int = 3600,
    tag_count: int = 8,
    allowed_categories: tuple[str, ...] = (
        "web_article",
        "note",
        "thought",
        "discussion_summary",
        "document",
        "reference",
        "repo",
        "paper",
        "social_post",
        "web",
        "dev",
        "test",
    ),
    llm_api_key: str = "test-small-llm-key",
    embedding_api_key: str = "test-embedding-key",
    embedding_base_url: str = "http://127.0.0.1:9/v1",
    embedding_model: str = "test-embedding",
    embedding_dim: int = 4,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "clawsqlite.toml"
    config_path.write_text(
        f"""
[knowledge]
root = "{root}"
db = "knowledge.sqlite3"
articles_dir = "articles"

[ingest]
require_llm = {str(require_llm).lower()}
require_embedding = {str(require_embedding).lower()}
summary_mode = "llm"
summary_target_chars = {summary_target_chars}
tags_mode = "llm"
tag_count = {tag_count}
allowed_categories = [{", ".join(f'"{x}"' for x in allowed_categories)}]
fallback = "fail"

[llm]
base_url = "http://127.0.0.1:9/v1"
model = "test-llm"
api_key = "{llm_api_key}"
context_window_chars = 4000
prompt_reserved_chars = 1000

[embedding]
base_url = "{embedding_base_url}"
model = "{embedding_model}"
api_key = "{embedding_api_key}"
dim = {embedding_dim}

[backup]
provider = "s3"

[backup.s3]
bucket = "test-bucket"
prefix = "test-prefix"
endpoint_url = "http://127.0.0.1:9"
region = "test-region"
access_key_id = "test-access-key"
secret_access_key = "test-secret-key"
""".lstrip(),
        encoding="utf-8",
    )
    return config_path
