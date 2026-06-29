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


def small_llm_enabled() -> bool:
    """Public helper: whether SMALL_LLM_* search/generation path is usable."""
    return _small_llm_enabled()

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


_CONTENT_TYPES = {
    "web_article",
    "note",
    "thought",
    "discussion_summary",
}


def _chunk_text(text: str, *, chunk_chars: int, overlap_chars: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunk_chars = max(1000, int(chunk_chars or 1000))
    overlap_chars = max(0, min(int(overlap_chars or 0), chunk_chars // 3))
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_chars)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(0, end - overlap_chars)
    return chunks


def _normalize_string_list(value: Any, *, max_items: int) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        s = str(item or "").strip()
        if not s:
            continue
        low = s.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _validate_llm_fields(obj: Dict[str, Any], *, fallback_title: str, min_tags: int = 5) -> Dict[str, Any]:
    title = str(obj.get("title") or fallback_title or "").strip()
    summary = str(obj.get("summary") or "").strip()
    tags = _normalize_tags(obj.get("tags", []), max_tags=12)
    key_claims = _normalize_string_list(obj.get("key_claims", []), max_items=12)
    entities = _normalize_string_list(obj.get("entities", []), max_items=20)
    content_type = str(obj.get("content_type") or "web_article").strip().lower()
    if content_type not in _CONTENT_TYPES:
        content_type = "web_article"

    if not title:
        raise RuntimeError("LLM generation failed validation: title is empty")
    if not summary:
        raise RuntimeError("LLM generation failed validation: summary is empty")
    if len(tags) < min_tags:
        raise RuntimeError("LLM generation failed validation: too few tags")

    return {
        "title": title,
        "summary": summary,
        "tags": tags,
        "key_claims": key_claims,
        "entities": entities,
        "content_type": content_type,
    }


def _llm_fields_once(
    content: str,
    *,
    hint_title: Optional[str],
    summary_target_chars: int,
    timeout: int,
    source_is_chunk_summaries: bool = False,
) -> Dict[str, Any]:
    title_hint = (hint_title or "").strip()
    source_label = "chunk summaries" if source_is_chunk_summaries else "full content"
    prompt = (
        "Extract structured metadata for a long-term personal knowledge base.\n"
        "Use the provided content only; do not invent facts.\n"
        "The summary will be embedded for semantic search, so it must capture the whole piece, not only the beginning.\n"
        "Constraints:\n"
        "- Output STRICT JSON only.\n"
        "- Keys: title, summary, tags, key_claims, entities, content_type.\n"
        f"- summary target length: about {summary_target_chars} characters; concise but information-dense.\n"
        "- tags: 5 to 12 short tags, sorted by importance descending.\n"
        "- key_claims: 3 to 8 short claims or takeaways.\n"
        "- entities: important people/projects/products/orgs/concepts.\n"
        "- content_type must be one of: web_article, note, thought, discussion_summary.\n"
        "- Do not add extra keys.\n\n"
        f"Title hint: {title_hint}\n"
        f"Input type: {source_label}\n\n"
        "Content:\n"
        f"{content}\n"
    )
    obj = _call_small_llm_json(prompt, timeout=timeout)
    return _validate_llm_fields(obj, fallback_title=title_hint)


def _llm_chunk_summary(chunk: str, *, index: int, total: int, timeout: int, target_chars: int) -> str:
    prompt = (
        "Summarize one chunk from a longer document for later synthesis.\n"
        "Use only this chunk. Output STRICT JSON only with key: summary.\n"
        f"Target summary length: about {target_chars} characters.\n"
        f"Chunk {index} of {total}:\n{chunk}\n"
    )
    obj = _call_small_llm_json(prompt, timeout=timeout)
    summary = str(obj.get("summary") or "").strip()
    if not summary:
        raise RuntimeError(f"LLM chunk summary failed validation: chunk {index}")
    return summary


def _llm_fields_from_content(
    content: str,
    *,
    hint_title: Optional[str],
    summary_target_chars: int,
    context_window_chars: int,
    prompt_reserved_chars: int,
    chunk_overlap_chars: int,
    timeout: int,
) -> Dict[str, Any]:
    budget = max(1000, int(context_window_chars) - int(prompt_reserved_chars))
    text = (content or "").strip()
    if len(text) <= budget:
        return _llm_fields_once(
            text,
            hint_title=hint_title,
            summary_target_chars=summary_target_chars,
            timeout=timeout,
        )

    chunks = _chunk_text(text, chunk_chars=budget, overlap_chars=chunk_overlap_chars)
    if not chunks:
        raise RuntimeError("LLM generation failed: no chunks produced")
    chunk_target = max(300, min(1200, summary_target_chars // 2))
    summaries: List[str] = []
    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        summaries.append(
            _llm_chunk_summary(
                chunk,
                index=i,
                total=total,
                timeout=timeout,
                target_chars=chunk_target,
            )
        )

    # Very long documents can still produce too many chunk summaries for the
    # final synthesis prompt. Keep compressing the summary list until the same
    # context-budget rule is true for the synthesis call as well.
    for _ in range(8):
        if len(summaries) <= 1:
            break
        synthesis_preview = "\n\n".join(
            f"Chunk {i} summary:\n{s}" for i, s in enumerate(summaries, start=1)
        )
        if len(synthesis_preview) <= budget:
            break
        grouped = _chunk_text(synthesis_preview, chunk_chars=budget, overlap_chars=0)
        next_summaries: List[str] = []
        group_total = len(grouped)
        for i, group in enumerate(grouped, start=1):
            next_summaries.append(
                _llm_chunk_summary(
                    group,
                    index=i,
                    total=group_total,
                    timeout=timeout,
                    target_chars=chunk_target,
                )
            )
        summaries = next_summaries

    synthesis_input = "\n\n".join(
        f"Chunk {i} summary:\n{s}" for i, s in enumerate(summaries, start=1)
    )
    return _llm_fields_once(
        synthesis_input,
        hint_title=hint_title,
        summary_target_chars=summary_target_chars,
        timeout=timeout,
        source_is_chunk_summaries=True,
    )


def _warn_llm_tags_fallback(reason: str) -> None:
    global _LLM_TAGS_WARNED
    if _LLM_TAGS_WARNED:
        return
    _LLM_TAGS_WARNED = True
    sys.stderr.write(
        "WARNING: LLM field generation is not available; falling back to heuristics.\n"
    )
    if reason:
        sys.stderr.write(f"WARNING: reason: {reason}\n")
    sys.stderr.write(
        "NEXT: configure [llm].base_url/model/api_key in clawsqlite.toml to enable LLM field generation.\n"
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


def _llm_refine_and_keywords_for_query(
    query: str,
    *,
    min_k: int = 8,
    max_k: int = 12,
) -> Dict[str, Any]:
    """Use SMALL_LLM to produce query_refine + query_tags for retrieval."""
    prompt = (
        "Rewrite the user query for retrieval and extract important search tags.\n"
        "Constraints:\n"
        "- Keep original intent; do NOT add new facts.\n"
        "- query_refine: one concise retrieval sentence.\n"
        f"- query_tags: {min_k} to {max_k} short keywords/phrases, sorted by importance.\n"
        "- Output STRICT JSON only with keys: query_refine, query_tags\n\n"
        f"User query:\n{query}\n"
    )
    obj = _call_small_llm_json(prompt)
    query_refine = str(obj.get("query_refine", "") or "").strip()
    query_tags = _normalize_tags(obj.get("query_tags", []), max_tags=max_k)
    return {"query_refine": query_refine, "query_tags": query_tags}


def generate_search_query_plan(
    query: str,
    *,
    provider: str,
    max_k: int = 12,
    min_k: int = 8,
) -> Dict[str, Any]:
    """Build search inputs: query_refine + query_tags.

    Behavior:
    - provider=llm and SMALL_LLM_* available: try LLM first.
    - otherwise (or on failure): fallback to heuristic extraction.
    """
    text = (query or "").strip()
    if not text:
        return {"query_refine": "", "query_tags": [], "used_llm": False}

    provider = (provider or "openclaw").strip().lower()
    max_k = max(1, int(max_k or 12))
    min_k = max(1, int(min_k or 8))
    if min_k > max_k:
        min_k = max_k

    if provider == "llm" and _small_llm_enabled():
        try:
            llm_out = _llm_refine_and_keywords_for_query(
                text,
                min_k=min_k,
                max_k=max_k,
            )
            query_refine = (llm_out.get("query_refine") or "").strip() or text
            query_tags = _normalize_tags(llm_out.get("query_tags", []), max_tags=max_k)
            if not query_tags:
                query_tags = _heuristic_keywords_for_query(query_refine, max_k=max_k)
            return {
                "query_refine": query_refine,
                "query_tags": query_tags[:max_k],
                "used_llm": True,
            }
        except Exception:
            pass

    query_refine = text
    query_tags = _heuristic_keywords_for_query(text, max_k=max_k)
    return {
        "query_refine": query_refine,
        "query_tags": query_tags[:max_k],
        "used_llm": False,
    }


def generate_fields(
    content: str,
    *,
    hint_title: Optional[str],
    provider: str,
    max_summary_chars: int = 1200,
    allow_heuristic: bool = True,
    llm_context_window_chars: int = 24000,
    llm_prompt_reserved_chars: int = 4000,
    llm_chunk_overlap_chars: int = 500,
    llm_timeout_seconds: int = 60,
) -> Dict[str, Any]:
    """
    Return dict with keys: title, summary, tags(list).
    """
    provider = (provider or "openclaw").strip().lower()

    if provider == "off":
        return {
            "title": hint_title or "",
            "summary": "",
            "tags": [],
            "generation_quality": "manual",
            "content_type": "note",
            "key_claims": [],
            "entities": [],
        }

    # Strip obvious metadata/header noise before heuristics.
    clean = _strip_metadata_for_generation(content)
    title = _heuristic_title(clean, hint_title=hint_title)
    summary = _build_long_summary(clean, target_words=1200, max_chars=max_summary_chars)

    if provider == "openclaw":
        tags = _heuristic_tags(summary)
        return {
            "title": title,
            "summary": summary,
            "tags": tags,
            "generation_quality": "heuristic",
            "content_type": "web_article",
            "key_claims": [],
            "entities": [],
        }

    if provider == "llm":
        if _small_llm_enabled():
            try:
                fields = _llm_fields_from_content(
                    clean,
                    hint_title=title or hint_title,
                    summary_target_chars=max_summary_chars,
                    context_window_chars=llm_context_window_chars,
                    prompt_reserved_chars=llm_prompt_reserved_chars,
                    chunk_overlap_chars=llm_chunk_overlap_chars,
                    timeout=llm_timeout_seconds,
                )
                fields["generation_quality"] = "llm"
                return fields
            except Exception as e:
                if not allow_heuristic:
                    raise
                _warn_llm_tags_fallback(str(e))
        else:
            if not allow_heuristic:
                raise RuntimeError("Small LLM is required but SMALL_LLM_* is incomplete")
            _warn_llm_tags_fallback("missing SMALL_LLM_*")
        tags = _heuristic_tags(summary)
        return {
            "title": title,
            "summary": summary,
            "tags": tags,
            "generation_quality": "heuristic",
            "content_type": "web_article",
            "key_claims": [],
            "entities": [],
        }

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
        plan = generate_search_query_plan(
            query,
            provider="llm",
            max_k=max_k,
            min_k=min(max_k, 5),
        )
        kws = plan.get("query_tags", [])
        if not isinstance(kws, list):
            kws = []
        if not kws:
            kws = _heuristic_keywords_for_query(query, max_k=max_k)
        return kws[:max_k]

    return _heuristic_keywords_for_query(query, max_k=max_k)
