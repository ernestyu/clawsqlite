# -*- coding: utf-8 -*-
"""
Field generator for clawsqlite knowledge.

Provider options:
- openclaw: default. Do NOT call an external LLM. If caller didn't provide fields, use heuristics.
- llm: call a configured LLM via OpenAI-compatible chat completions API to generate fields.
- off: do nothing.
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

def _llm_enabled() -> bool:
    return bool(os.environ.get("LLM_MODEL") and os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY"))


def llm_enabled() -> bool:
    """Public helper: whether the configured runtime LLM path is usable."""
    return _llm_enabled()


def _strip_json_fence(content: str) -> str:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n", "", text)
        text = re.sub(r"\n```$", "", text).strip()
    return text


def _extract_json_object_text(content: str) -> str:
    text = _strip_json_fence(content)
    if not text:
        return text
    if text.lstrip().startswith("{") and text.rstrip().endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text

    # Prefer a balanced object when prose surrounds the JSON. If malformed
    # quotes confuse the scanner, fall back to the widest object-looking span.
    in_str = False
    escape = False
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1].strip()
    return text[start : end + 1].strip()


def _quote_looks_structural(text: str, quote_index: int) -> bool:
    j = quote_index + 1
    while j < len(text) and text[j].isspace():
        j += 1
    if j >= len(text):
        return True
    nxt = text[j]
    if nxt == ":":
        return True
    if nxt in "}]" or nxt == "\n":
        return True
    if nxt == ",":
        j += 1
        while j < len(text) and text[j].isspace():
            j += 1
        return j >= len(text) or text[j] in '"}]'
    return False


def _escape_unescaped_quotes_in_json_strings(text: str) -> str:
    out: List[str] = []
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\" and in_str:
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            if not in_str:
                in_str = True
                out.append(ch)
            elif _quote_looks_structural(text, i):
                in_str = False
                out.append(ch)
            else:
                out.append('\\"')
            continue
        out.append(ch)
    return "".join(out)


def _coerce_scalar_json_value(value: str) -> Any:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except Exception:
        pass
    low = raw.lower()
    if low == "null":
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    return raw.strip().strip('"').strip("'")


def _salvage_json_fields(text: str) -> Dict[str, Any]:
    src = _extract_json_object_text(text)
    fields: Dict[str, Any] = {}
    key_re = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
    matches = list(key_re.finditer(src))
    for idx, match in enumerate(matches):
        key = match.group(1)
        value_start = match.end()
        value_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(src)
        raw = src[value_start:value_end].strip().rstrip(",").rstrip("}")
        if not raw:
            continue
        if raw.startswith('"'):
            chars: List[str] = []
            escape = False
            i = 1
            while i < len(raw):
                ch = raw[i]
                if escape:
                    chars.append(ch)
                    escape = False
                    i += 1
                    continue
                if ch == "\\":
                    escape = True
                    i += 1
                    continue
                if ch == '"' and _quote_looks_structural(raw, i):
                    break
                chars.append(ch)
                i += 1
            fields[key] = "".join(chars).strip()
        elif raw.startswith("["):
            end = raw.rfind("]")
            arr_text = raw[: end + 1] if end != -1 else raw
            repaired = _escape_unescaped_quotes_in_json_strings(arr_text)
            try:
                parsed = json.loads(repaired)
                fields[key] = parsed if isinstance(parsed, list) else [parsed]
            except Exception:
                items = re.findall(r'"((?:\\.|[^"\\])*)"', arr_text)
                fields[key] = [item.replace('\\"', '"').strip() for item in items if item.strip()]
        else:
            fields[key] = _coerce_scalar_json_value(raw)
    return fields


def _loads_llm_json_content(content: str) -> Dict[str, Any]:
    attempts: List[str] = []
    text = _strip_json_fence(content)
    attempts.append(text)
    candidate = _extract_json_object_text(text)
    if candidate and candidate not in attempts:
        attempts.append(candidate)
    repaired = _escape_unescaped_quotes_in_json_strings(candidate)
    if repaired and repaired not in attempts:
        attempts.append(repaired)

    last_error: Optional[Exception] = None
    for attempt in attempts:
        try:
            parsed = json.loads(attempt)
            if isinstance(parsed, dict):
                return parsed
        except Exception as e:
            last_error = e

    salvaged = _salvage_json_fields(candidate or text)
    if salvaged:
        return salvaged
    raise ValueError(str(last_error or "no JSON object found"))


def _llm_json_payload(prompt: str, *, json_mode: bool = True) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": os.environ.get("LLM_MODEL"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a concise assistant. Output ONLY one valid JSON object. "
                    "Do not wrap it in Markdown. Escape ASCII double quotes inside JSON strings."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _post_llm_chat_json(prompt: str, *, timeout: int, json_mode: bool) -> Dict[str, Any]:
    model = os.environ.get("LLM_MODEL")
    base_url = os.environ.get("LLM_BASE_URL")
    api_key = os.environ.get("LLM_API_KEY")
    if not (model and base_url and api_key):
        raise RuntimeError("LLM is not enabled: configured model/base_url/api_key are incomplete")
    url = _chat_url(base_url)

    payload = _llm_json_payload(prompt, json_mode=json_mode)
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
        raise RuntimeError(f"LLM HTTPError: {e.code} {e.reason} {err_body}")
    except Exception as e:
        raise RuntimeError(f"LLM request failed: {e}")

    try:
        obj = json.loads(body)
    except Exception as e:
        raise RuntimeError(f"LLM response parse failed: {e}; body={body[:300]}")
    try:
        content = str(obj["choices"][0]["message"]["content"] or "")
    except Exception as e:
        raise RuntimeError(f"LLM response missing message content: {e}; body={body[:300]}")
    try:
        return _loads_llm_json_content(content)
    except Exception as e:
        raise RuntimeError(f"LLM JSON parse failed: {e}; content={content[:1000]}; body={body[:300]}")


def _call_llm_json(prompt: str, *, timeout: int = 60) -> Dict[str, Any]:
    parse_error: Optional[RuntimeError] = None
    try:
        return _post_llm_chat_json(prompt, timeout=timeout, json_mode=True)
    except RuntimeError as first_error:
        err_text = str(first_error)
        if "response_format" in err_text or "json_object" in err_text or "400" in err_text or "422" in err_text:
            try:
                return _post_llm_chat_json(prompt, timeout=timeout, json_mode=False)
            except RuntimeError as second_error:
                parse_error = second_error
        else:
            parse_error = first_error
        repair_prompt = (
            "Fix the following assistant output into one valid JSON object only.\n"
            "Rules:\n"
            "- Preserve all factual content.\n"
            "- Escape ASCII double quotes inside string values.\n"
            "- Do not add markdown, comments, or explanations.\n\n"
            f"Parse error:\n{str(parse_error or first_error)[:1800]}\n\n"
            f"Original task:\n{prompt[:2000]}\n"
        )
        try:
            return _post_llm_chat_json(repair_prompt, timeout=timeout, json_mode=True)
        except RuntimeError as repair_error:
            repair_text = str(repair_error)
            if "response_format" in repair_text or "json_object" in repair_text or "400" in repair_text or "422" in repair_text:
                try:
                    return _post_llm_chat_json(repair_prompt, timeout=timeout, json_mode=False)
                except RuntimeError:
                    pass
            raise parse_error or first_error

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

_BAD_TITLES = {"", "untitled", "无标题", "未命名", "标题"}


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


def _estimate_text_tokens(text: str) -> int:
    """Conservative token estimate for mixed English/CJK content.

    We avoid a model-specific tokenizer dependency because deployments use
    OpenAI-compatible endpoints with different tokenizers. This estimator is
    intentionally cautious for CJK text, where characters often map close to
    individual tokens.
    """

    tokens = 0
    ascii_run = 0

    def flush_ascii() -> None:
        nonlocal tokens, ascii_run
        if ascii_run:
            tokens += max(1, (ascii_run + 3) // 4)
            ascii_run = 0

    for ch in text or "":
        code = ord(ch)
        if ch.isspace():
            flush_ascii()
            continue
        if 0x4E00 <= code <= 0x9FFF:
            flush_ascii()
            tokens += 1
        elif code < 128:
            ascii_run += 1
        else:
            flush_ascii()
            tokens += 1
    flush_ascii()
    return tokens


def _llm_input_token_budget(context_window_tokens: int, *, summary_target_chars: int) -> int:
    context = max(2048, int(context_window_tokens or 8192))
    prompt_reserve = max(512, min(1536, context // 6))
    output_reserve = max(512, min(max(512, int(summary_target_chars or 0) + 256), context // 2))
    return max(512, context - prompt_reserve - output_reserve)


def _overlap_token_budget(input_budget_tokens: int) -> int:
    return max(0, min(256, int(input_budget_tokens) // 12))


def _chunk_text_by_tokens(text: str, *, chunk_tokens: int, overlap_tokens: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunk_tokens = max(512, int(chunk_tokens or 512))
    overlap_tokens = max(0, min(int(overlap_tokens or 0), chunk_tokens // 3))

    chunks: List[str] = []
    n = len(text)
    start = 0
    while start < n:
        tokens = 0
        ascii_run = 0
        end = start
        last_break = -1
        while end < n:
            ch = text[end]
            code = ord(ch)
            if ch in "\n。！？；.!?;":
                last_break = end + 1
            if ch.isspace():
                if ascii_run:
                    tokens += max(1, (ascii_run + 3) // 4)
                    ascii_run = 0
            elif 0x4E00 <= code <= 0x9FFF:
                if ascii_run:
                    tokens += max(1, (ascii_run + 3) // 4)
                    ascii_run = 0
                tokens += 1
            elif code < 128:
                ascii_run += 1
            else:
                if ascii_run:
                    tokens += max(1, (ascii_run + 3) // 4)
                    ascii_run = 0
                tokens += 1
            pending = tokens + (max(1, (ascii_run + 3) // 4) if ascii_run else 0)
            if pending > chunk_tokens:
                break
            end += 1
        if end < n and last_break > start and last_break - start >= 200:
            end = last_break
        if end <= start:
            end = min(n, start + 1)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        if overlap_tokens <= 0:
            start = end
            continue
        overlap_start = end
        seen = 0
        while overlap_start > start and seen < overlap_tokens:
            overlap_start -= 1
            seen = _estimate_text_tokens(text[overlap_start:end])
        start = max(start + 1, overlap_start)
    return chunks


def _select_head_tail_chunks(chunks: List[str], *, max_chunks: int) -> List[str]:
    max_chunks = max(1, int(max_chunks or 1))
    if len(chunks) <= max_chunks:
        return chunks
    if max_chunks == 1:
        return chunks[:1]
    head_count = max_chunks - 1
    return chunks[:head_count] + [chunks[-1]]


def _fit_text_to_token_budget(text: str, *, budget_tokens: int) -> str:
    text = (text or "").strip()
    if _estimate_text_tokens(text) <= budget_tokens:
        return text
    half_budget = max(512, int(budget_tokens) // 2)
    pieces = _chunk_text_by_tokens(text, chunk_tokens=half_budget, overlap_tokens=0)
    if not pieces:
        return ""
    if len(pieces) == 1:
        return pieces[0]
    candidate = f"{pieces[0]}\n\n{pieces[-1]}".strip()
    if _estimate_text_tokens(candidate) <= budget_tokens:
        return candidate
    fitted = _chunk_text_by_tokens(candidate, chunk_tokens=budget_tokens, overlap_tokens=0)
    return fitted[0] if fitted else ""


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


def _validate_title(title: str) -> str:
    out = _normalize_space(title)
    if out.lower() in _BAD_TITLES:
        raise RuntimeError("LLM generation failed validation: title is empty or generic")
    if "\n" in title or "\r" in title:
        raise RuntimeError("LLM generation failed validation: title must be one line")
    if len(out) < 2:
        raise RuntimeError("LLM generation failed validation: title is too short")
    if len(out) > 120:
        raise RuntimeError("LLM generation failed validation: title is too long")
    return out


def _validate_llm_fields(
    obj: Dict[str, Any],
    *,
    fallback_title: str,
    tag_count: int = 8,
    allowed_content_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    title = _validate_title(str(obj.get("title") or fallback_title or "").strip())
    summary = str(obj.get("summary") or "").strip()
    tags = _normalize_tags(obj.get("tags", []), max_tags=max(1, tag_count))
    key_claims = _normalize_string_list(obj.get("key_claims", []), max_items=12)
    entities = _normalize_string_list(obj.get("entities", []), max_items=20)
    content_type = str(obj.get("content_type") or "web_article").strip().lower()
    category = str(obj.get("category") or "").strip().lower()
    allowed = set(allowed_content_types or sorted(_CONTENT_TYPES))
    if not category:
        raise RuntimeError("LLM generation failed validation: category is empty")
    if category not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise RuntimeError(f"LLM generation failed validation: category must be one of: {allowed_text}")
    if content_type not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise RuntimeError(f"LLM generation failed validation: content_type must be one of: {allowed_text}")
    if category != content_type:
        raise RuntimeError("LLM generation failed validation: category and content_type must match")

    if not summary:
        raise RuntimeError("LLM generation failed validation: summary is empty")
    if len(tags) != tag_count:
        raise RuntimeError(f"LLM generation failed validation: expected exactly {tag_count} tags")

    return {
        "title": title,
        "summary": summary,
        "tags": tags,
        "key_claims": key_claims,
        "entities": entities,
        "category": category,
        "content_type": content_type,
    }


def _llm_fields_once(
    content: str,
    *,
    hint_title: Optional[str],
    hint_tags: Optional[str],
    summary_target_chars: int,
    tag_count: int,
    allowed_content_types: Optional[List[str]],
    timeout: int,
    preserve_short_content: bool = False,
    source_is_chunk_summaries: bool = False,
) -> Dict[str, Any]:
    title_hint = (hint_title or "").strip()
    tags_hint = (hint_tags or "").strip()
    allowed_text = ", ".join(allowed_content_types or sorted(_CONTENT_TYPES))
    source_label = "chunk summaries" if source_is_chunk_summaries else "full content"
    short_rule = (
        "- For short direct notes, summary should preserve the original content instead of paraphrasing away details.\n"
        if preserve_short_content
        else "- For web articles and scraped pages, summarize the content even when the cleaned input is short.\n"
    )
    prompt = (
        "Extract structured metadata for a long-term personal knowledge base.\n"
        "Use the provided content only; do not invent facts.\n"
        "Treat title and tag hints as optional hints, not as authoritative final metadata.\n"
        "The summary will be embedded for semantic search, so it must capture the whole piece, not only the beginning.\n"
        "Constraints:\n"
        "- Output STRICT JSON only.\n"
        "- Output exactly one JSON object, with no markdown fences or explanatory text.\n"
        "- Inside JSON string values, escape any ASCII double quote as \\\". Prefer Chinese quotation marks when quoting phrases.\n"
        "- Keys: title, summary, tags, category, content_type, key_claims, entities.\n"
        "- title: one line, specific, 2 to 120 characters; never output generic titles like untitled.\n"
        f"- summary target length: about {summary_target_chars} characters; concise but information-dense.\n"
        f"{short_rule}"
        f"- tags: exactly {tag_count} short tags, sorted by importance descending.\n"
        "- tags must be a JSON array of strings, not a comma-separated string.\n"
        "- key_claims: 3 to 8 short claims or takeaways.\n"
        "- entities: important people/projects/products/orgs/concepts.\n"
        f"- category must be one of: {allowed_text}.\n"
        f"- content_type must be one of: {allowed_text}.\n"
        "- category and content_type must be identical in this version.\n"
        "- Do not add extra keys.\n\n"
        f"Title hint: {title_hint}\n"
        f"Tag hints: {tags_hint}\n"
        f"Input type: {source_label}\n\n"
        "Content:\n"
        f"{content}\n"
    )
    obj = _call_llm_json(prompt, timeout=timeout)
    return _validate_llm_fields(
        obj,
        fallback_title=title_hint,
        tag_count=tag_count,
        allowed_content_types=allowed_content_types,
    )


def _llm_chunk_summary(chunk: str, *, index: int, total: int, timeout: int, target_chars: int) -> str:
    prompt = (
        "Summarize one chunk from a longer document for later synthesis.\n"
        "Use only this chunk. Output STRICT JSON only with key: summary.\n"
        "Output exactly one JSON object. Do not use markdown fences or explanatory text.\n"
        "Inside the JSON summary string, escape any ASCII double quote as \\\".\n"
        f"Target summary length: about {target_chars} characters.\n"
        f"Chunk {index} of {total}:\n{chunk}\n"
    )
    obj = _call_llm_json(prompt, timeout=timeout)
    summary = str(obj.get("summary") or "").strip()
    if not summary:
        raise RuntimeError(f"LLM chunk summary failed validation: chunk {index}")
    return summary


def _llm_fields_from_content(
    content: str,
    *,
    hint_title: Optional[str],
    hint_tags: Optional[str],
    summary_target_chars: int,
    tag_count: int,
    allowed_content_types: Optional[List[str]],
    context_window_tokens: int,
    max_chunks_per_article: int,
    timeout: int,
    preserve_short_content: bool = False,
) -> Dict[str, Any]:
    budget_tokens = _llm_input_token_budget(
        context_window_tokens,
        summary_target_chars=summary_target_chars,
    )
    text = (content or "").strip()
    if _estimate_text_tokens(text) <= budget_tokens:
        return _llm_fields_once(
            text,
            hint_title=hint_title,
            hint_tags=hint_tags,
            summary_target_chars=summary_target_chars,
            tag_count=tag_count,
            allowed_content_types=allowed_content_types,
            timeout=timeout,
            preserve_short_content=preserve_short_content,
        )

    chunks = _chunk_text_by_tokens(
        text,
        chunk_tokens=budget_tokens,
        overlap_tokens=_overlap_token_budget(budget_tokens),
    )
    if not chunks:
        raise RuntimeError("LLM generation failed: no chunks produced")
    selected_chunks = _select_head_tail_chunks(
        chunks,
        max_chunks=max_chunks_per_article,
    )
    selected_total = len(selected_chunks)
    if selected_total == 1:
        return _llm_fields_once(
            selected_chunks[0],
            hint_title=hint_title,
            hint_tags=hint_tags,
            summary_target_chars=summary_target_chars,
            tag_count=tag_count,
            allowed_content_types=allowed_content_types,
            timeout=timeout,
            preserve_short_content=False,
        )

    chunk_target = max(300, min(1200, summary_target_chars // max(1, selected_total)))
    summaries: List[str] = []
    for i, chunk in enumerate(selected_chunks, start=1):
        summaries.append(
            _llm_chunk_summary(
                chunk,
                index=i,
                total=selected_total,
                timeout=timeout,
                target_chars=chunk_target,
            )
        )

    synthesis_input = "\n\n".join(
        f"Chunk {i} summary:\n{s}" for i, s in enumerate(summaries, start=1)
    )
    synthesis_input = _fit_text_to_token_budget(synthesis_input, budget_tokens=budget_tokens)
    return _llm_fields_once(
        synthesis_input,
        hint_title=hint_title,
        hint_tags=hint_tags,
        summary_target_chars=summary_target_chars,
        tag_count=tag_count,
        allowed_content_types=allowed_content_types,
        timeout=timeout,
        preserve_short_content=False,
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
    """Use the configured LLM to produce query_refine + query_tags for retrieval."""
    prompt = (
        "Rewrite the user query for retrieval and extract important search tags.\n"
        "Constraints:\n"
        "- Output exactly one JSON object, with no markdown fences or explanatory text.\n"
        "- Inside JSON string values, escape any ASCII double quote as \\\".\n"
        "- Keep original intent; do NOT add new facts.\n"
        "- query_refine: one concise retrieval sentence.\n"
        f"- query_tags: {min_k} to {max_k} short keywords/phrases, sorted by importance.\n"
        "- Output STRICT JSON only with keys: query_refine, query_tags\n\n"
        f"User query:\n{query}\n"
    )
    obj = _call_llm_json(prompt)
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
    - provider=llm and configured runtime LLM values are complete: try LLM first.
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

    if provider == "llm" and _llm_enabled():
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
    tag_count: int = 8,
    allowed_content_types: Optional[List[str]] = None,
    hint_tags: Optional[str] = None,
    allow_heuristic: bool = True,
    llm_context_window_tokens: int = 8192,
    llm_max_chunks_per_article: int = 3,
    llm_context_window_chars: int = 0,
    llm_prompt_reserved_chars: int = 0,
    llm_chunk_overlap_chars: int = 0,
    llm_timeout_seconds: int = 60,
    source_kind: str = "",
    source_content_type: str = "",
) -> Dict[str, Any]:
    """
    Return dict with keys: title, summary, tags(list).
    """
    provider = (provider or "openclaw").strip().lower()
    tag_count = max(1, int(tag_count or 8))

    if provider == "off":
        return {
            "title": hint_title or "",
            "summary": "",
            "tags": [],
            "generation_quality": "manual",
            "category": "note",
            "content_type": "note",
            "key_claims": [],
            "entities": [],
        }

    # Strip obvious metadata/header noise before heuristics.
    clean = _strip_metadata_for_generation(content)
    title = _heuristic_title(clean, hint_title=hint_title)
    summary = _build_long_summary(clean, target_words=1200, max_chars=max_summary_chars)
    source_kind_norm = (source_kind or "").strip().lower()
    source_content_type_norm = (source_content_type or "").strip().lower()
    passthrough_types = {"note", "thought", "discussion_summary"}
    short_summary_passthrough = bool(
        clean
        and len(clean) <= max_summary_chars
        and (source_kind_norm == "text" or source_content_type_norm in passthrough_types)
    )

    if provider == "openclaw":
        tags = _heuristic_tags(summary, max_tags=tag_count)
        return {
            "title": title,
            "summary": summary,
            "tags": tags,
            "generation_quality": "heuristic",
            "category": "web_article",
            "content_type": "web_article",
            "key_claims": [],
            "entities": [],
        }

    if provider == "llm":
        if _llm_enabled():
            try:
                fields = _llm_fields_from_content(
                    clean,
                    hint_title=title or hint_title,
                    hint_tags=hint_tags,
                    summary_target_chars=max_summary_chars,
                    tag_count=tag_count,
                    allowed_content_types=allowed_content_types,
                    context_window_tokens=llm_context_window_tokens,
                    max_chunks_per_article=llm_max_chunks_per_article,
                    timeout=llm_timeout_seconds,
                    preserve_short_content=short_summary_passthrough,
                )
                if short_summary_passthrough:
                    fields["summary"] = clean
                fields["generation_quality"] = "llm"
                return fields
            except Exception as e:
                if not allow_heuristic:
                    raise
                _warn_llm_tags_fallback(str(e))
        else:
            if not allow_heuristic:
                raise RuntimeError("LLM is required but configured runtime values are incomplete")
            _warn_llm_tags_fallback("missing configured runtime LLM values")
        tags = _heuristic_tags(summary, max_tags=tag_count)
        return {
            "title": title,
            "summary": summary,
            "tags": tags,
            "generation_quality": "heuristic",
            "category": "web_article",
            "content_type": "web_article",
            "key_claims": [],
            "entities": [],
        }

    raise RuntimeError(f"Unknown gen provider: {provider}")

def generate_keywords_for_search(query: str, *, provider: str, max_k: int = 10) -> List[str]:
    """
    Generate keywords for FTS query expansion.

    For provider=openclaw/off: use query_v4 extraction.
    For provider=llm: use the configured LLM if enabled.
    """
    provider = (provider or "openclaw").strip().lower()
    if provider in ("off", "openclaw"):
        return _heuristic_keywords_for_query(query, max_k=max_k)

    if provider == "llm":
        if not _llm_enabled():
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
