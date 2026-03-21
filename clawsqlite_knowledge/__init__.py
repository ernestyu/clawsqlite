# -*- coding: utf-8 -*-
"""clawsqlite_knowledge package.

Knowledge application layer for clawsqlite. This package currently wraps

- the legacy `clawkb` CLI (via `knowledge_cli`), and
- small helper/wrapper utilities that bridge to `clawsqlite_plumbing`.

Over time this will be refactored to depend more heavily on the plumbing
layer and remove `clawkb` naming from internal modules as well.
"""
from __future__ import annotations

__all__ = ["knowledge_cli", "reindex_wrappers"]
