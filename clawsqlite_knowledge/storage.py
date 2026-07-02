# -*- coding: utf-8 -*-
"""
Markdown storage for clawsqlite knowledge.
"""
from __future__ import annotations

import os
from .utils import slugify

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def article_relpath(article_id: int, title: str) -> str:
    slug = slugify(title)
    return f"{article_id:06d}__{slug}.md"

def article_abspath(articles_dir: str, article_id: int, title: str) -> str:
    return os.path.join(articles_dir, article_relpath(article_id, title))

def write_markdown(path: str, content: str) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def read_markdown(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def format_markdown_with_metadata(
    *,
    article_id: int,
    title: str,
    source_url: str,
    created_at: str,
    category: str,
    tags: str,
    priority: int,
    body_markdown: str,
    summary: str = "",
    generation_quality: str = "",
    summary_model: str = "",
    tags_model: str = "",
    embedding_model: str = "",
    content_type: str = "",
    key_claims: str = "",
) -> str:
    header = (
        "--- METADATA ---\n"
        f"id: {article_id}\n"
        f"title: {title}\n"
        f"source_url: {source_url}\n"
        f"created_at: {created_at}\n"
        f"category: {category}\n"
        f"tags: {tags}\n"
        f"priority: {priority}\n"
        f"generation_quality: {generation_quality}\n"
        f"summary_model: {summary_model}\n"
        f"tags_model: {tags_model}\n"
        f"embedding_model: {embedding_model}\n"
        f"content_type: {content_type}\n"
        "--- SUMMARY ---\n"
        f"{summary}\n"
        "--- KEY CLAIMS ---\n"
        f"{key_claims}\n"
        "--- MARKDOWN ---\n"
    )
    body = body_markdown if body_markdown.endswith("\n") else body_markdown + "\n"
    return header + body
