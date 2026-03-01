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
import urllib.request
import urllib.error
from typing import List, Optional, Tuple

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
    """
    Call embedding service and return a float list.

    Raises RuntimeError on failures.
    """
    model = os.environ.get("EMBEDDING_MODEL")
    base_url = os.environ.get("EMBEDDING_BASE_URL")
    api_key = os.environ.get("EMBEDDING_API_KEY")
    if not (model and base_url and api_key):
        raise RuntimeError("Embedding is not enabled: missing EMBEDDING_MODEL/BASE_URL/API_KEY")
    url = _embeddings_url(base_url)

    payload = json.dumps({"model": model, "input": text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Embedding HTTPError: {e.code} {e.reason} {err_body}")
    except Exception as e:
        raise RuntimeError(f"Embedding request failed: {e}")

    try:
        data = json.loads(body)
        # OpenAI-style response: {"data":[{"embedding":[...]}], ...}
        emb = data["data"][0]["embedding"]
        if not isinstance(emb, list):
            raise ValueError("Invalid embedding format")
        return [float(x) for x in emb]
    except Exception as e:
        raise RuntimeError(f"Embedding response parse failed: {e}; body={body[:300]}")

def floats_to_f32_blob(vec: List[float], *, dim: int = 1536) -> bytes:
    if len(vec) != dim:
        raise ValueError(f"Embedding dim mismatch: got {len(vec)}, expected {dim}")
    # pack as little-endian float32
    return struct.pack("<" + "f" * dim, *[float(x) for x in vec])
