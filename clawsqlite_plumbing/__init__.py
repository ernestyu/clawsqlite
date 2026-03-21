# -*- coding: utf-8 -*-
"""Plumbing layer for clawsqlite.

This package implements low-level, generic commands that back the
`clawsqlite db/index/fs` namespaces.

Design goals:
- generic (no knowledge-specific tables like `articles` baked in),
- predictable (small, focused operations),
- callable from higher-level apps (knowledge/reading/etc.).
"""
from __future__ import annotations

__all__ = [
    "db_cli",
    "index_cli",
    "fs_cli",
]
