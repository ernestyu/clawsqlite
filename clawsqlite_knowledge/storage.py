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


def article_db_relpath(article_id: int, title: str) -> str:
    return f"articles/{article_relpath(article_id, title)}"


def resolve_local_file_path(path_value: str, instance_root: str) -> str:
    p = (path_value or "").strip()
    if not p:
        return ""
    if os.path.isabs(p):
        normalized = os.path.normpath(p)
        if os.path.exists(normalized):
            return normalized
        parts = normalized.split(os.sep)
        if "articles" in parts:
            idx = parts.index("articles")
            rel = os.path.join(*parts[idx:])
            return os.path.normpath(os.path.join(instance_root, rel))
        return normalized
    return os.path.normpath(os.path.join(instance_root, p))


def relativize_local_file_path(abs_path: str, instance_root: str) -> str:
    p = (abs_path or "").strip()
    if not p:
        return ""
    if not os.path.isabs(p):
        return os.path.normpath(p)
    try:
        rel = os.path.relpath(p, instance_root)
        if rel != ".." and not rel.startswith(".." + os.sep):
            return os.path.normpath(rel)
    except Exception:
        pass
    parts = os.path.normpath(p).split(os.sep)
    if "articles" in parts:
        idx = parts.index("articles")
        return os.path.normpath(os.path.join(*parts[idx:]))
    return os.path.normpath(p)


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
    source_title: str,
    generated_title: str,
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
        f"source_title: {source_title}\n"
        f"generated_title: {generated_title}\n"
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
