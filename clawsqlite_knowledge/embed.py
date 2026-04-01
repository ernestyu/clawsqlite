# -*- coding: utf-8 -*-
"""
Embedding client for clawsqlite_knowledge.

Uses global env vars:
- EMBEDDING_MODEL
- EMBEDDING_BASE_URL
- EMBEDDING_API_KEY

We assume an OpenAI-compatible embeddings API.
"""
from __future__ import annotations

import os
import json
import struct
from typing import List

import httpx

def _embedding_missing_keys() -> list[str]:
    missing: list[str] = []
    for key in ("EMBEDDING_MODEL", "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY"):
        if not os.environ.get(key):
            missing.append(key)
    dim_env = os.environ.get("CLAWSQLITE_VEC_DIM")
    if not dim_env:
        missing.append("CLAWSQLITE_VEC_DIM")
    else:
        try:
            dim = int(dim_env)
            if dim <= 0:
                raise ValueError
        except Exception:
            missing.append("CLAWSQLITE_VEC_DIM")
    return missing


def embedding_enabled() -> bool:
    """Return True if vector embedding is fully configured.

    We require both the embedding API triple and a vec-dim env
    (CLAWSQLITE_VEC_DIM).
    If any of these are missing, vector features are treated as disabled
    (FTS still works normally).
    """

    return not _embedding_missing_keys()

def _embeddings_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    # If user provides a full path, keep it.
    if base.endswith("/embeddings"):
        return base
    if base.endswith("/v1"):
        return base + "/embeddings"
    # If it looks like it already contains /v1/..., do not duplicate.
    if "/v1/" in base:
        return base + "/embeddings"
    return base + "/v1/embeddings" if base.startswith("http") else base + "/embeddings"

def get_embedding(text: str, *, timeout: int = 300) -> List[float]:
    """Call embedding service and return a float list.

    Uses httpx with a sane default User-Agent to avoid being trivially
    blocked by WAF/CDN layers. Raises RuntimeError on failures.
    """

    model = os.environ.get("EMBEDDING_MODEL")
    base_url = os.environ.get("EMBEDDING_BASE_URL")
    api_key = os.environ.get("EMBEDDING_API_KEY")
    if not (model and base_url and api_key):
        # This is a programmer/config error; CLI callers should surface
        # a NEXT hint alongside the exception message.
        raise RuntimeError("Embedding is not enabled: missing EMBEDDING_MODEL/BASE_URL/API_KEY")

    url = _embeddings_url(base_url)
    payload = {"model": model, "input": text}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        # A realistic UA helps avoid naive 403/1010 style blocks.
        "User-Agent": os.environ.get(
            "CLAWSQLITE_HTTP_USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) Clawsqlite/1.0",
        ),
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except Exception as e:
        raise RuntimeError(f"Embedding request failed: {e}")

    if resp.status_code >= 400:
        snippet = resp.text[:300]
        raise RuntimeError(
            f"Embedding HTTPError: {resp.status_code} {resp.reason_phrase} {snippet}"
        )

    body = resp.text
    try:
        data = json.loads(body)
        # OpenAI-style response: {"data":[{"embedding":[...]}], ...}
        emb = data["data"][0]["embedding"]
        if not isinstance(emb, list):
            raise ValueError("Invalid embedding format")
        return [float(x) for x in emb]
    except Exception as e:
        raise RuntimeError(f"Embedding response parse failed: {e}; body={body[:300]}")

def _resolve_vec_dim() -> int:
    """Resolve vector dimension from env/project config.

    Priority:
    1. CLAWSQLITE_VEC_DIM env (must be a positive integer)

    Note: dimension must be consistent with the articles_vec schema.
    """

    dim_env = os.environ.get("CLAWSQLITE_VEC_DIM")
    if not dim_env:
        raise ValueError("CLAWSQLITE_VEC_DIM is required for embeddings")
    try:
        dim = int(dim_env)
    except Exception:
        raise ValueError("CLAWSQLITE_VEC_DIM must be an integer")
    if dim <= 0:
        raise ValueError("CLAWSQLITE_VEC_DIM must be a positive integer")
    return dim


def floats_to_f32_blob(vec: List[float], *, dim: int | None = None) -> bytes:
    if dim is None:
        dim = _resolve_vec_dim()
    if len(vec) != dim:
        raise ValueError(f"Embedding dim mismatch: got {len(vec)}, expected {dim}")
    # pack as little-endian float32
    return struct.pack("<" + "f" * dim, *[float(x) for x in vec])
