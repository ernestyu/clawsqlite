# -*- coding: utf-8 -*-
"""
Field generator for clawkb.

Provider options:
- openclaw: default. Do NOT call external small LLM. If caller didn't provide fields, use heuristics.
- llm: call a small LLM via OpenAI-compatible chat completions API (SMALL_LLM_* env).
- off: do nothing.

Global env vars for small LLM:
- SMALL_LLM_MODEL
- SMALL_LLM_BASE_URL
- SMALL_LLM_API_KEY
"""
from __future__ import annotations

import os
import json
import re
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from .utils import truncate_text, comma_join_tags, extract_keywords_light

def _chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    if "/v1/" in base:
        return base + "/chat/completions"
    return base + "/v1/chat/completions" if base.startswith("http") else base + "/chat/completions"

def _small_llm_enabled() -> bool:
    return bool(os.environ.get("SMALL_LLM_MODEL") and os.environ.get("SMALL_LLM_BASE_URL") and os.environ.get("SMALL_LLM_API_KEY"))

def _call_small_llm_json(prompt: str, *, timeout: int = 60) -> Dict[str, Any]:
    model = os.environ.get("SMALL_LLM_MODEL")
    base_url = os.environ.get("SMALL_LLM_BASE_URL")
    api_key = os.environ.get("SMALL_LLM_API_KEY")
    if not (model and base_url and api_key):
        raise RuntimeError("Small LLM is not enabled: missing SMALL_LLM_MODEL/BASE_URL/API_KEY")
    url = _chat_url(base_url)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a concise assistant. Output ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
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
        raise RuntimeError(f"Small LLM HTTPError: {e.code} {e.reason} {err_body}")
    except Exception as e:
        raise RuntimeError(f"Small LLM request failed: {e}")

    try:
        obj = json.loads(body)
        content = obj["choices"][0]["message"]["content"]
        # Some models wrap JSON in code fences; strip them.
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```[a-zA-Z]*\n", "", content)
            content = re.sub(r"\n```$", "", content).strip()
        return json.loads(content)
    except Exception as e:
        raise RuntimeError(f"Small LLM parse failed: {e}; body={body[:300]}")

def _heuristic_title(content: str, hint_title: Optional[str] = None) -> str:
    if hint_title and hint_title.strip():
        return hint_title.strip()
    c = (content or "").strip()
    if not c:
        return "untitled"
    # Use first non-empty line
    for line in c.splitlines():
        t = line.strip()
        if t:
            # Cut very long lines
            return (t[:80]).strip()
    return (c[:80]).strip()

def _heuristic_summary(content: str, max_chars: int = 800) -> str:
    c = (content or "").strip()
    if not c:
        return ""
    # Simple: take first N chars, but try to preserve paragraph boundary
    return truncate_text(c, max_chars=max_chars)

def _heuristic_tags(content: str, max_tags: int = 8) -> List[str]:
    kws = extract_keywords_light(content, max_k=max_tags)
    # Keep unique, short tags
    out = []
    seen = set()
    for k in kws:
        k = k.strip()
        if not k:
            continue
        if len(k) > 24:
            continue
        if k.lower() in seen:
            continue
        seen.add(k.lower())
        out.append(k)
        if len(out) >= max_tags:
            break
    return out

def generate_fields(
    content: str,
    *,
    hint_title: Optional[str],
    provider: str,
    max_summary_chars: int = 1200,
) -> Dict[str, Any]:
    """
    Return dict with keys: title, summary, tags(list).
    """
    provider = (provider or "openclaw").strip().lower()

    if provider == "off":
        return {"title": hint_title or "", "summary": "", "tags": []}

    if provider == "openclaw":
        # Default: avoid external LLM calls. Assume OpenClaw caller may pass fields.
        title = _heuristic_title(content, hint_title=hint_title)
        summary = truncate_text(_heuristic_summary(content, max_chars=min(800, max_summary_chars)), max_chars=max_summary_chars)
        tags = _heuristic_tags(content)
        return {"title": title, "summary": summary, "tags": tags}

    if provider == "llm":
        if not _small_llm_enabled():
            raise RuntimeError("provider=llm but SMALL_LLM_* env vars are missing")
        prompt = (
            "Given the content below, generate a good title, a long summary, and tags.\n"
            "Constraints:\n"
            "- Output STRICT JSON with keys: title (string), summary (string), tags (array of strings)\n"
            "- summary must be a long summary but NOT the full content\n"
            "- tags should be 5 to 12 short items\n"
            "- Do NOT add any extra keys\n\n"
            "Content:\n"
            f"{content}\n"
        )
        obj = _call_small_llm_json(prompt)
        title = str(obj.get("title", "")).strip() or _heuristic_title(content, hint_title=hint_title)
        summary = str(obj.get("summary", "")).strip()
        tags = obj.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip() for t in tags if str(t).strip()]
        summary = truncate_text(summary or _heuristic_summary(content), max_chars=max_summary_chars)
        return {"title": title, "summary": summary, "tags": tags}

    raise RuntimeError(f"Unknown gen provider: {provider}")

def generate_keywords_for_search(query: str, *, provider: str, max_k: int = 10) -> List[str]:
    """
    Generate keywords for FTS query expansion.

    For provider=openclaw/off: use lightweight extraction.
    For provider=llm: use small LLM if enabled.
    """
    provider = (provider or "openclaw").strip().lower()
    if provider in ("off", "openclaw"):
        return extract_keywords_light(query, max_k=max_k)

    if provider == "llm":
        if not _small_llm_enabled():
            return extract_keywords_light(query, max_k=max_k)
        prompt = (
            "Extract keywords for searching a personal knowledge base.\n"
            "Output STRICT JSON: {\"keywords\": [\"...\"]}\n"
            f"Query: {query}\n"
        )
        obj = _call_small_llm_json(prompt)
        kws = obj.get("keywords", [])
        if not isinstance(kws, list):
            kws = []
        kws = [str(x).strip() for x in kws if str(x).strip()]
        if not kws:
            kws = extract_keywords_light(query, max_k=max_k)
        return kws[:max_k]

    return extract_keywords_light(query, max_k=max_k)
