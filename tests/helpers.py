# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def write_knowledge_config(
    root: Path,
    *,
    require_llm: bool = False,
    require_embedding: bool = False,
    summary_target_chars: int = 800,
    llm_api_key: str = "test-small-llm-key",
    embedding_api_key: str = "test-embedding-key",
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
fallback = "fail"

[llm]
base_url = "http://127.0.0.1:9/v1"
model = "test-llm"
api_key = "{llm_api_key}"
context_window_chars = 4000
prompt_reserved_chars = 1000

[embedding]
base_url = "http://127.0.0.1:9/v1"
model = "test-embedding"
api_key = "{embedding_api_key}"
dim = 4
""".lstrip(),
        encoding="utf-8",
    )
    return config_path
