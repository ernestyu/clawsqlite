# -*- coding: utf-8 -*-
"""Wrappers that bridge knowledge commands to clawsqlite plumbing.

Currently minimal: provide helper(s) for:
- mapping knowledge's `reindex --rebuild` into `clawsqlite index` primitives.

This keeps the knowledge CLI surface stable while allowing internal
refactors to use the new plumbing layer.
"""
from __future__ import annotations

from typing import Dict

from clawsqlite_plumbing import index_cli


def rebuild_indexes_via_plumbing(db_path: str, *, rebuild_fts: bool, rebuild_vec: bool) -> Dict[str, object]:
    """Call plumbing `clawsqlite index` commands for KB default tables.

    Assumes the usual KB schema:
    - base table: `articles`
    - FTS table: `articles_fts`
    - vec table: `articles_vec`

    Returns a small dict summarizing what was done.
    """
    result: Dict[str, object] = {"fts_rebuilt": False, "vec_cleared": False}

    if rebuild_fts or rebuild_vec:
        argv = [
            "rebuild",
            "--db",
            db_path,
            "--table",
            "articles",
        ]
        if rebuild_fts:
            argv.extend(["--fts-table", "articles_fts"])
        if rebuild_vec:
            argv.extend(["--vec-table", "articles_vec"])

        code = index_cli.main(argv)
        result["exit_code"] = int(code)
        if rebuild_fts:
            result["fts_rebuilt"] = True
        if rebuild_vec:
            # plumbing currently clears vec table; app is responsible for
            # repopulating embeddings.
            result["vec_cleared"] = True

    return result
