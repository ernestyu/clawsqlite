# -*- coding: utf-8 -*-
"""
Field generator for clawsqlite knowledge.

Provider options:
- openclaw: default. Do NOT call external small LLM. If caller didn't provide fields, use heuristics.
- llm: call a small LLM via OpenAI-compatible chat completions API (SMALL_LLM_* env) to generate tags.
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
from typing import Any, Dict, List, Optional

from .utils import truncate_text, extract_keywords_light
from .tagger_v6 import heuristic_tags as _heuristic_tags_v6
from .query_keywords_v4 import extract_query_keywords as _extract_query_keywords_v4

_LLM_TAGS_WARNED = False


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

    # 1.1) Strip YAML-style front matter if present.
    if c.startswith("---"):
        m = re.match(r"^---\s*\n.*?\n---\s*(?:\n|$)", c, flags=re.DOTALL)
        if m:
            c = c[m.end() :].lstrip("\r\n")

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

_WORD_UNIT_RE = re.compile(r"[A-Za-z0-9]+(?:[._+\-/][A-Za-z0-9]+)*|[\u4e00-\u9fff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _count_word_units(text: str) -> int:
    return len(_WORD_UNIT_RE.findall(text or ""))


def _split_paragraphs(text: str) -> List[str]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = [p for p in re.split(r"\n\s*\n+", text) if p and p.strip()]
    out: List[str] = []
    for p in parts:
        norm = _normalize_space(p)
        if norm:
            out.append(norm)
    if out:
        if len(out) == 1 and "\n" in text:
            lines = [_normalize_space(x) for x in text.split("\n") if _normalize_space(x)]
            if len(lines) > 1:
                return lines
        return out
    single = _normalize_space(text)
    return [single] if single else []


def _build_long_summary(
    content: str,
    *,
    target_words: int = 1200,
    max_chars: Optional[int] = None,
) -> str:
    text = (content or "").strip()
    if not text:
        return ""

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return ""

    head_paragraphs: List[str] = []
    count = 0
    for p in paragraphs:
        if not p:
            continue
        head_paragraphs.append(p)
        count += _count_word_units(p)
        if count >= max(1, target_words):
            break
    if not head_paragraphs:
        head_paragraphs = [paragraphs[0]]

    head_text = "\n\n".join(head_paragraphs).strip()
    tail = (paragraphs[-1] or "").strip()

    if tail and tail != head_paragraphs[-1]:
        combined = (head_text + "\n\n" + tail).strip() if head_text else tail
    else:
        combined = head_text

    if not max_chars or max_chars <= 0:
        return combined.strip()
    if len(combined) <= max_chars:
        return combined.strip()

    if not tail:
        return truncate_text(combined, max_chars=max_chars)
    if len(tail) >= max_chars:
        return truncate_text(tail, max_chars=max_chars)

    available_head = max_chars - len(tail) - (2 if head_text else 0)
    if available_head <= 0:
        return tail.strip()

    head_fit: List[str] = []
    for p in paragraphs:
        if not p:
            continue
        candidate = p if not head_fit else "\n\n".join(head_fit + [p])
        if len(candidate) <= available_head:
            head_fit.append(p)
        else:
            if not head_fit:
                head_fit = [truncate_text(p, max_chars=available_head)]
            break
    head_fit_text = "\n\n".join([h for h in head_fit if h]).strip()
    if not head_fit_text:
        return tail.strip()
    if tail and head_fit_text.endswith(tail):
        return head_fit_text
    return (head_fit_text + "\n\n" + tail).strip()


def _heuristic_tags(content: str, max_tags: int = 8) -> List[str]:
    return _heuristic_tags_v6(content or "", max_tags=max_tags)


def _normalize_tags(tags: Any, *, max_tags: int) -> List[str]:
    if not isinstance(tags, list):
        return []
    out: List[str] = []
    seen = set()
    for t in tags:
        s = str(t).strip()
        if not s:
            continue
        low = s.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(s)
        if len(out) >= max_tags:
            break
    return out


def _llm_tags_from_summary(summary: str, *, max_tags: int = 12) -> List[str]:
    prompt = (
        "Generate tags for a knowledge base from the long summary below.\n"
        "Constraints:\n"
        "- Output STRICT JSON with key: tags (array of strings)\n"
        "- tags should be 5 to 12 short items\n"
        "- Order tags by importance descending (most important first)\n"
        "- Do NOT add any extra keys\n\n"
        "Long summary:\n"
        f"{summary}\n"
    )
    obj = _call_small_llm_json(prompt)
    tags = obj.get("tags", [])
    return _normalize_tags(tags, max_tags=max_tags)


def _warn_llm_tags_fallback(reason: str) -> None:
    global _LLM_TAGS_WARNED
    if _LLM_TAGS_WARNED:
        return
    _LLM_TAGS_WARNED = True
    sys.stderr.write(
        "WARNING: LLM tag generation is not available; falling back to jieba.\n"
    )
    if reason:
        sys.stderr.write(f"WARNING: reason: {reason}\n")
    sys.stderr.write(
        "NEXT: set SMALL_LLM_BASE_URL/SMALL_LLM_MODEL/SMALL_LLM_API_KEY to enable LLM tag generation.\n"
    )


def _heuristic_keywords_for_query(query: str, max_k: int = 10) -> List[str]:
    """Heuristic keyword extraction for search queries (query_v4)."""
    text = (query or "").strip()
    if not text:
        return []
    # query_v4 is tuned for natural-language CJK queries; for pure ASCII
    # queries we keep behavior simple and stable.
    if not _CJK_RE.search(text):
        return extract_keywords_light(text, max_k=max_k)
    try:
        out = _extract_query_keywords_v4(text, max_k=max_k)
    except Exception:
        out = []
    if not out:
        out = extract_keywords_light(text, max_k=max_k)
    return out[:max_k]


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

    # Strip obvious metadata/header noise before heuristics.
    clean = _strip_metadata_for_generation(content)
    title = _heuristic_title(clean, hint_title=hint_title)
    summary = _build_long_summary(clean, target_words=1200, max_chars=max_summary_chars)

    if provider == "openclaw":
        tags = _heuristic_tags(summary)
        return {"title": title, "summary": summary, "tags": tags}

    if provider == "llm":
        tags: List[str] = []
        if _small_llm_enabled():
            try:
                tags = _llm_tags_from_summary(summary, max_tags=12)
            except Exception as e:
                _warn_llm_tags_fallback(str(e))
                tags = _heuristic_tags(summary)
        else:
            _warn_llm_tags_fallback("missing SMALL_LLM_*")
            tags = _heuristic_tags(summary)
        if not tags and summary:
            tags = _heuristic_tags(summary)
        return {"title": title, "summary": summary, "tags": tags}

    raise RuntimeError(f"Unknown gen provider: {provider}")

def generate_keywords_for_search(query: str, *, provider: str, max_k: int = 10) -> List[str]:
    """
    Generate keywords for FTS query expansion.

    For provider=openclaw/off: use query_v4 extraction.
    For provider=llm: use small LLM if enabled.
    """
    provider = (provider or "openclaw").strip().lower()
    if provider in ("off", "openclaw"):
        return _heuristic_keywords_for_query(query, max_k=max_k)

    if provider == "llm":
        if not _small_llm_enabled():
            return _heuristic_keywords_for_query(query, max_k=max_k)
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
            kws = _heuristic_keywords_for_query(query, max_k=max_k)
        return kws[:max_k]

    return _heuristic_keywords_for_query(query, max_k=max_k)
