# -*- coding: utf-8 -*-
"""
Embedding client for clawkb.

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

def embedding_enabled() -> bool:
    return bool(os.environ.get("EMBEDDING_MODEL") and os.environ.get("EMBEDDING_BASE_URL") and os.environ.get("EMBEDDING_API_KEY"))

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

def get_embedding(text: str, *, timeout: int = 60) -> List[float]:
    """Call embedding service and return a float list.

    Uses httpx with a sane default User-Agent to avoid being trivially
    blocked by WAF/CDN layers. Raises RuntimeError on failures.
    """

    model = os.environ.get("EMBEDDING_MODEL")
    base_url = os.environ.get("EMBEDDING_BASE_URL")
    api_key = os.environ.get("EMBEDDING_API_KEY")
    if not (model and base_url and api_key):
        raise RuntimeError("Embedding is not enabled: missing EMBEDDING_MODEL/BASE_URL/API_KEY")

    url = _embeddings_url(base_url)
    payload = {"model": model, "input": text}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        # A realistic UA helps avoid naive 403/1010 style blocks.
        "User-Agent": os.environ.get(
            "CLAWKB_HTTP_USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) Clawkb/1.0",
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
    1. CLAWKB_VEC_DIM env (e.g. from project .env)
    2. Fallback to 1536 to preserve legacy behavior when no config is set.

    Note: dimension must be consistent with the articles_vec schema.
    """

    dim_env = os.environ.get("CLAWKB_VEC_DIM")
    if dim_env:
        try:
            return int(dim_env)
        except Exception:
            pass
    # Legacy default
    return 1536


def floats_to_f32_blob(vec: List[float], *, dim: int | None = None) -> bytes:
    if dim is None:
        dim = _resolve_vec_dim()
    if len(vec) != dim:
        raise ValueError(f"Embedding dim mismatch: got {len(vec)}, expected {dim}")
    # pack as little-endian float32
    return struct.pack("<" + "f" * dim, *[float(x) for x in vec])
