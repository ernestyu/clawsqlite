# -*- coding: utf-8 -*-
"""
Field generator for clawsqlite knowledge.

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
import sys
import json
import re
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from .utils import truncate_text, comma_join_tags, extract_keywords_light, has_cjk
from .embed import embedding_enabled, get_embedding

# Optional Chinese tokenizer/keywords: jieba
try:  # pragma: no cover - optional dependency
    import jieba  # type: ignore
    import jieba.analyse as _jieba_analyse  # type: ignore
    _JIEBA_AVAILABLE = True
except Exception:  # ImportError or others
    _jieba_analyse = None
    _JIEBA_AVAILABLE = False

# Avoid spamming warnings if jieba is missing; only warn once per process.
_JIEBA_WARNED = False


def _tags_semantic_mode() -> str:
    """Return CLAWSQLITE_TAGS_SEMANTIC mode (auto|on|off)."""
    return (os.environ.get("CLAWSQLITE_TAGS_SEMANTIC") or "auto").strip().lower()


def _semantic_rerank_candidates(snippet: str, candidates: List[str], max_tags: int) -> List[str]:
    """Re-rank candidate tags using semantic centrality when embedding is enabled.

    This is an opt-in high-end path:
    - We require embeddings to be configured (embedding_enabled()).
    - We treat the snippet as the "sentence" vector.
    - Each candidate tag is embedded; we compute cosine similarity
      between the tag vector and the snippet vector.
    - Candidates are re-sorted by this similarity in descending order.

    If anything fails (missing embedding, HTTP error, etc.), we fall back
    to the original candidate order.
    """

    if not candidates:
        return candidates

    if not embedding_enabled():  # quick exit if embedding is not configured
        return candidates

    snippet = (snippet or "").strip()
    if not snippet:
        return candidates

    try:
        sent_vec = get_embedding(snippet)
    except Exception:
        return candidates

    # Pre-compute norm of sentence vector
    try:
        import math as _math

        sent_norm = _math.sqrt(sum((float(x) ** 2 for x in sent_vec))) or 1.0
    except Exception:
        return candidates

    scored: List[tuple[str, float]] = []
    for k in candidates:
        text = (k or "").strip()
        if not text:
            scored.append((k, 0.0))
            continue
        try:
            w_vec = get_embedding(text)
            w_norm = _math.sqrt(sum((float(x) ** 2 for x in w_vec))) or 1.0
            dot = sum((float(a) * float(b) for a, b in zip(sent_vec, w_vec)))
            cos = dot / (sent_norm * w_norm)
        except Exception:
            cos = 0.0
        scored.append((k, float(cos)))

    # Sort by cosine similarity (descending); keep original order as tie-breaker
    scored_sorted = sorted(
        enumerate(scored), key=lambda t: t[1][1], reverse=True
    )
    reordered: List[str] = []
    for idx, (_k, _score) in scored_sorted:
        reordered.append(scored[idx][0])
        if len(reordered) >= max_tags * 3:
            break
    return reordered


def _strip_metadata_for_generation(content: str) -> str:
    """Strip obvious metadata/header noise before generating fields.

    This is intentionally conservative and only targets patterns we know are
    meta rather than real content, for example:

    - Our own structured markdown with "--- MARKDOWN ---" separator
    - WeChat-style reading stats like "字数 1136，阅读大约需 6 分钟"
    """
    if not content:
        return content

    c = content.lstrip()

    # 1) If this is our own structured markdown, prefer the MARKDOWN section.
    #
    # format_markdown_with_metadata writes:
    #   --- METADATA ---
    #   ...
    #   --- SUMMARY ---
    #   ...
    #   --- MARKDOWN ---
    #   <body>
    #
    # For generation we only care about the body.
    marker = "--- MARKDOWN ---"
    idx = c.find(marker)
    if idx != -1:
        return c[idx + len(marker) :].lstrip("\r\n")

    # 2) Strip leading "字数/阅读时间" 行（常见于微信公众号）。
    lines = c.splitlines()
    cleaned: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if i <= 2 and stripped:
            # Typical pattern: "字数 1136，阅读大约需 6 分钟 ..."
            if ("字数" in stripped and "阅读" in stripped and "分钟" in stripped):
                # Drop this line entirely.
                continue
        cleaned.append(line)
    return "\n".join(cleaned)


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

def _heuristic_tags(content: str, max_tags: int = 8, *, snippet_chars: int = 800) -> List[str]:
    """Heuristic tag extraction.

    Preference order:
    - If embeddings + jieba are available and CLAWSQLITE_TAGS_SEMANTIC is
      `auto`/`on`, use jieba TextRank to get candidates and re-rank them via
      semantic centrality against the snippet embedding.
    - Else if jieba is available, use jieba.analyse.textrank on the first
      `snippet_chars` characters (falling back to TF-IDF on error).
    - Otherwise, fall back to extract_keywords_light on the same snippet.

    All paths then apply simple filters to drop numeric/meta tokens and
    keep only the first `max_tags` items.
    """

    snippet = (content or "")[:snippet_chars]

    candidates: List[str]
    if _JIEBA_AVAILABLE:
        # Prefer TextRank for better global importance; fall back to TF-IDF on error.
        try:
            candidates = list(_jieba_analyse.textrank(snippet, topK=max_tags * 3))
        except Exception:
            try:
                candidates = list(_jieba_analyse.extract_tags(snippet, topK=max_tags * 3))
            except Exception:
                candidates = extract_keywords_light(snippet, max_k=max_tags * 3)

        # Optional semantic re-ranking when embeddings are configured.
        mode = _tags_semantic_mode()
        if mode in {"auto", "on"}:
            try:
                candidates = _semantic_rerank_candidates(snippet, candidates, max_tags)
            except Exception:
                # Fall back silently to the original candidate order.
                pass
    else:
        # No jieba: fall back to lightweight extractor, but emit a one-time hint.
        global _JIEBA_WARNED
        if not _JIEBA_WARNED:
            _JIEBA_WARNED = True
            sys.stderr.write(
                "NEXT: jieba is not installed; tags fall back to a lightweight "
                "keyword extractor. For better Chinese tags, install jieba via "
                "'pip install jieba'.\n"
            )
        candidates = extract_keywords_light(snippet, max_k=max_tags * 3)

    # Simple blacklist for obvious meta tokens
    blacklist = {"字数", "阅读", "阅读大约需", "分钟", "min", "mins"}
    out: List[str] = []
    seen = set()
    for k in candidates:
        k = k.strip()
        if not k:
            continue
        # Drop almost pure numbers
        if len(k) <= 3 and any(ch.isdigit() for ch in k) and not any(ch.isalpha() for ch in k):
            continue
        if any(b in k for b in blacklist):
            continue
        if len(k) > 24:
            continue
        low = k.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(k)
        if len(out) >= max_tags:
            break
    return out


def _heuristic_keywords_for_query(query: str, max_k: int = 10) -> List[str]:
    """Heuristic keyword extraction for search queries.

    Preference order (mirrors tag extraction but on the query text):
    - If embeddings + jieba are available and CLAWSQLITE_TAGS_SEMANTIC is
      `auto`/`on`, use jieba TextRank to get candidates and re-rank them via
      semantic centrality against the query embedding.
    - Else if jieba is available, use jieba.analyse.textrank on the query
      (falling back to TF-IDF on error).
    - Otherwise, fall back to extract_keywords_light.
    """

    text = (query or "").strip()
    if not text:
        return []
    snippet = text[:400]

    candidates: List[str]
    # Only use jieba-based extraction when the query actually contains CJK;
    # for pure ASCII we keep things simple and use extract_keywords_light.
    if _JIEBA_AVAILABLE and _jieba_analyse is not None and has_cjk(snippet, threshold=1):
        try:
            candidates = list(_jieba_analyse.textrank(snippet, topK=max_k * 3))
        except Exception:
            try:
                candidates = list(_jieba_analyse.extract_tags(snippet, topK=max_k * 3))
            except Exception:
                candidates = extract_keywords_light(snippet, max_k=max_k * 3)

        if not candidates:
            candidates = extract_keywords_light(snippet, max_k=max_k * 3)

        mode = _tags_semantic_mode()
        if mode in {"auto", "on"}:
            try:
                candidates = _semantic_rerank_candidates(snippet, candidates, max_k)
            except Exception:
                pass
    else:
        # No jieba (or no CJK): fall back to lightweight extractor. If jieba
        # is missing entirely, emit a one-time NEXT hint.
        global _JIEBA_WARNED
        if not _JIEBA_WARNED and not _JIEBA_AVAILABLE:
            _JIEBA_WARNED = True
            sys.stderr.write(
                "NEXT: jieba is not installed; query keywords fall back to a "
                "lightweight extractor. For better Chinese search, install "
                "jieba via 'pip install jieba'.\n"
            )
        candidates = extract_keywords_light(snippet, max_k=max_k * 3)

    out: List[str] = []
    seen = set()
    for k in candidates:
        k = (k or "").strip()
        if not k:
            continue
        if len(k) > 24:
            continue
        low = k.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(k)
        if len(out) >= max_k:
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
        # Strip obvious metadata/header noise before heuristics.
        clean = _strip_metadata_for_generation(content)
        title = _heuristic_title(clean, hint_title=hint_title)
        summary = truncate_text(_heuristic_summary(clean, max_chars=min(800, max_summary_chars)), max_chars=max_summary_chars)
        tags = _heuristic_tags(clean)
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
        return _heuristic_keywords_for_query(query, max_k=max_k)

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
