#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
extract_tag_v6.py

Article-level tag extraction without embeddings, v6.

This version keeps the v5 document-wide phrase inventory design, but makes the
selection stage more tag-oriented:
- stronger generic structural cleanup
- stronger multi-token phrase preference
- stronger penalties for broad singleton terms
- better suppression of generic section / metadata words
- phrase-first final selection with later singleton backfill
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, List, Sequence, Tuple

try:
    import jieba  # noqa: F401
    import jieba.analyse as _jieba_analyse  # type: ignore
    import jieba.posseg as _pseg  # type: ignore
    _JIEBA_AVAILABLE = True
except Exception:
    jieba = None
    _jieba_analyse = None
    _pseg = None
    _JIEBA_AVAILABLE = False

_JIEBA_WARNED = False

ALLOWED_POS = ("n", "nr", "ns", "nt", "nz", "vn", "eng")
NOUNISH_POS = {"n", "nr", "ns", "nt", "nz", "vn", "eng"}

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+\-/]{1,31}")
_CJK_CHUNK_RE = re.compile(r"[\u3400-\u9fff]{2,16}")
_SPLIT_RE = re.compile(r"[\n。！？；;：:，,、（）()\[\]{}<>《》\"'“”‘’/]+")

_GENERIC_SHELL_EXACT = {
    "title", "titles", "subtitle", "author", "authors", "date", "tags", "tag",
    "category", "categories", "summary", "description", "desc", "readme",
    "toc", "content", "contents", "keywords", "keyword",
    "标题", "作者", "日期", "标签", "关键词", "摘要", "简介", "目录", "正文",
    "来源", "原文", "字数", "阅读", "分钟",
}

_GENERIC_STRUCTURE_EXACT = {
    "导语", "导读", "前言", "引言", "序言", "正文", "摘要", "简介", "目录",
    "小结", "总结", "结语", "后记", "附录", "参考", "参考文献", "注释",
    "readme", "overview", "introduction", "summary", "conclusion", "appendix",
}

_GENERIC_META_KEY_RE = re.compile(r"^(?:title|subtitle|author|date|tags?|categories?|summary|description|desc|toc|keywords?)\s*:", re.I)


def read_text(file_path: Path) -> str:
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return file_path.read_text(encoding="utf-8", errors="ignore")


def clean_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[>#\-\*\+\s]+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~]+", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_metadata_for_generation(content: str) -> str:
    if not content:
        return content

    c = content.lstrip()
    marker = "--- MARKDOWN ---"
    idx = c.find(marker)
    if idx != -1:
        c = c[idx + len(marker):].lstrip("\r\n")

    if c.startswith("---"):
        m = re.match(r"^---\s*\n.*?\n---\s*(?:\n|$)", c, flags=re.DOTALL)
        if m:
            c = c[m.end():]

    lines = c.splitlines()
    cleaned: List[str] = []
    head_meta_run = 0
    body_started = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue

        if i <= 3 and ("字数" in stripped and "阅读" in stripped and "分钟" in stripped):
            continue

        if not body_started and head_meta_run < 12:
            if _GENERIC_META_KEY_RE.match(stripped):
                head_meta_run += 1
                continue
            if re.match(r"^[\u4e00-\u9fffA-Za-z][\w\u4e00-\u9fff /+\-]{0,20}\s*:\s*.+$", stripped):
                key = stripped.split(":", 1)[0].strip().lower()
                if key in _GENERIC_SHELL_EXACT or key in _GENERIC_STRUCTURE_EXACT:
                    head_meta_run += 1
                    continue

        body_started = True
        cleaned.append(line)
    return "\n".join(cleaned)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def has_cjk(text: str, threshold: int = 1) -> bool:
    return len(_CJK_RE.findall(text or "")) >= threshold


def is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def is_englishish(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9+\-_. /()]+", text or ""))


def is_valid_token(token: str) -> bool:
    token = normalize_space(token)
    if not token:
        return False
    if re.fullmatch(r"[\W_]+", token):
        return False
    if re.fullmatch(r"\d+", token):
        return False
    if len(token) == 1 and not re.fullmatch(r"[A-Za-z]", token):
        return False
    return True


def generic_shell_term(term: str) -> bool:
    t = normalize_space(term).strip()
    if not t:
        return True
    low = t.lower()
    if low in _GENERIC_SHELL_EXACT or low in _GENERIC_STRUCTURE_EXACT:
        return True
    if re.fullmatch(r"(?:h1|h2|h3|h4|h5|h6)", low):
        return True
    if low.startswith(("title:", "author:", "date:", "summary:", "keyword:")):
        return True
    return False


def split_units(text: str) -> List[str]:
    return [u.strip() for u in _SPLIT_RE.split(text or "") if u.strip()]


def split_paragraphs(text: str) -> List[str]:
    text = text or ""
    parts = [normalize_space(p) for p in re.split(r"\n\s*\n+", text) if normalize_space(p)]
    if not parts:
        single = normalize_space(text)
        return [single] if single else []
    return parts


def chunk_text(text: str, target_chars: int = 360, overlap: int = 80) -> List[str]:
    text = normalize_space(text)
    if not text:
        return []
    if len(text) <= target_chars:
        return [text]

    chunks: List[str] = []
    start = 0
    step = max(40, target_chars - overlap)
    while start < len(text):
        end = min(len(text), start + target_chars)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start += step
    return chunks


def get_title_and_body(text: str) -> Tuple[str, str]:
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return "", ""
    title = lines[0]
    body = "\n".join(lines[1:]).strip()
    return title, body


def extract_keywords_light(text: str, max_k: int = 20) -> List[str]:
    tokens: List[str] = []
    tokens.extend(_ASCII_TOKEN_RE.findall(text or ""))
    tokens.extend(_CJK_CHUNK_RE.findall(text or ""))

    counter = Counter()
    for tok in tokens:
        tok = tok.strip()
        if not tok or tok.isdigit() or len(tok) <= 1:
            continue
        counter[tok] += 1

    ranked = sorted(counter.items(), key=lambda x: (-x[1], -len(x[0]), x[0].lower()))
    return [k for k, _ in ranked[:max_k]]


def pos_tokens(text: str) -> List[Tuple[str, str]]:
    if not (_JIEBA_AVAILABLE and _pseg is not None):
        return []
    out: List[Tuple[str, str]] = []
    for item in _pseg.cut(normalize_space(text)):
        word = item.word.strip()
        flag = item.flag
        if is_valid_token(word):
            out.append((word, flag))
    return out


def plain_tokens(text: str) -> List[str]:
    toks = pos_tokens(text)
    if toks:
        return [w for w, _ in toks if is_valid_token(w)]

    ascii_terms = _ASCII_TOKEN_RE.findall(text or "")
    cjk_terms = re.findall(r"[\u3400-\u9fff]", text or "")
    out: List[str] = []
    out.extend(ascii_terms)
    out.extend(cjk_terms)
    return out


def join_tokens(tokens: Sequence[str]) -> str:
    tokens = [t for t in tokens if t]
    if not tokens:
        return ""
    if all(is_cjk(t) for t in tokens):
        return "".join(tokens)
    if all(is_englishish(t) for t in tokens):
        return " ".join(tokens)
    out = ""
    for i, tok in enumerate(tokens):
        if i == 0:
            out = tok
            continue
        prev = tokens[i - 1]
        if is_englishish(prev) and is_englishish(tok):
            out += " " + tok
        else:
            out += tok
    return out


def phrase_length_ok(term: str) -> bool:
    term = normalize_space(term)
    if not term:
        return False
    if is_englishish(term):
        wc = len(term.split())
        return 1 <= wc <= 5
    return 2 <= len(term) <= 24


def sentence_like_penalty(term: str) -> float:
    term = normalize_space(term)
    if not term:
        return 1.0

    penalty = 0.0
    if is_englishish(term):
        wc = len(term.split())
        if wc >= 5:
            penalty += 1.0
        elif wc == 4:
            penalty += 0.3

    if is_cjk(term):
        if len(term) >= 14:
            penalty += 0.8
        elif len(term) >= 10:
            penalty += 0.25

    toks = pos_tokens(term)
    if toks:
        flags = [f for _, f in toks]
        noun_ratio = sum(1 for f in flags if f in NOUNISH_POS) / max(len(flags), 1)
        if noun_ratio < 0.4:
            penalty += 0.9
        elif noun_ratio < 0.7:
            penalty += 0.25

    return penalty


def near_duplicate(a: str, b: str) -> bool:
    a_n = normalize_space(a).lower()
    b_n = normalize_space(b).lower()
    if not a_n or not b_n:
        return False
    if a_n == b_n:
        return True
    if a_n in b_n or b_n in a_n:
        shorter = min(len(a_n), len(b_n))
        longer = max(len(a_n), len(b_n))
        if shorter >= 2 and longer <= shorter + 6:
            return True
    return False


def extract_textrank_candidates(text: str, top_k: int) -> Dict[str, float]:
    if not (_JIEBA_AVAILABLE and _jieba_analyse is not None and has_cjk(text, threshold=1)):
        return {}
    try:
        tags = _jieba_analyse.textrank(text, topK=top_k, withWeight=True, allowPOS=ALLOWED_POS)
        return {normalize_space(k): float(v) for k, v in tags if normalize_space(k)}
    except Exception:
        try:
            tags = _jieba_analyse.extract_tags(text, topK=top_k, withWeight=True, allowPOS=ALLOWED_POS)
            return {normalize_space(k): float(v) for k, v in tags if normalize_space(k)}
        except Exception:
            return {}


def extract_tfidf_candidates(text: str, top_k: int) -> Dict[str, float]:
    if not (_JIEBA_AVAILABLE and _jieba_analyse is not None):
        return {}
    try:
        tags = _jieba_analyse.extract_tags(text, topK=top_k, withWeight=True, allowPOS=ALLOWED_POS)
        return {normalize_space(k): float(v) for k, v in tags if normalize_space(k)}
    except Exception:
        return {}


def extract_local_noun_phrases(text: str, max_n: int = 4) -> List[str]:
    phrases: set[str] = set()
    if not (_JIEBA_AVAILABLE and _pseg is not None):
        return []

    for unit in split_units(text):
        toks = pos_tokens(unit)
        if not toks:
            continue

        chain: List[str] = []
        for word, flag in toks + [("", "")]:
            if flag in NOUNISH_POS:
                chain.append(word)
            else:
                if len(chain) >= 2:
                    max_take = min(max_n, len(chain))
                    for n in range(2, max_take + 1):
                        for i in range(0, len(chain) - n + 1):
                            phrase = join_tokens(chain[i:i + n])
                            if phrase_length_ok(phrase):
                                phrases.add(phrase)
                chain = []
    return sorted(phrases)


def extract_quoted_phrases(text: str) -> List[str]:
    out: List[str] = []
    patterns = [
        r'"([^"\n]{1,48})"',
        r"'([^'\n]{1,48})'",
        r"“([^”\n]{1,48})”",
        r"‘([^’\n]{1,48})’",
        r"《([^》\n]{1,48})》",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            term = normalize_space(m.group(1))
            if phrase_length_ok(term) and is_valid_token(term):
                out.append(term)
    return out


def extract_ascii_phrases(text: str) -> List[str]:
    found = _ASCII_TOKEN_RE.findall(text or "")
    return [normalize_space(x) for x in found if phrase_length_ok(x)]


def build_windows(title: str, body: str) -> List[Tuple[str, float, str]]:
    windows: List[Tuple[str, float, str]] = []
    title = normalize_space(title)
    body = body or ""
    lead = normalize_space(body[:1200]) if body else ""
    paragraphs = split_paragraphs(body)

    if title:
        windows.append((title, 2.5, "title"))
    if lead:
        windows.append((lead, 1.6, "lead"))

    for p in paragraphs:
        if len(p) < 8:
            continue
        # Drop generic structural headings as evidence windows.
        if generic_shell_term(p) and len(p) <= 12:
            continue
        windows.append((p, 1.0, "paragraph"))
        for chunk in chunk_text(p, target_chars=280, overlap=60):
            windows.append((chunk, 0.55, "chunk"))

    if not windows:
        merged = normalize_space(title + "\n" + body)
        if merged:
            windows.append((merged, 1.0, "full"))
    return windows


def add_phrase_support(
    phrase_support: DefaultDict[str, float],
    token_support: DefaultDict[str, float],
    edge_support: DefaultDict[Tuple[str, str], float],
    token_phrase_contexts: DefaultDict[str, set[str]],
    token_neighbor_contexts: DefaultDict[str, set[str]],
    source: Dict[str, float],
    window_weight: float,
) -> None:
    for cand, raw_weight in source.items():
        cand = normalize_space(cand)
        if not (cand and phrase_length_ok(cand) and is_valid_token(cand)):
            continue
        if generic_shell_term(cand):
            continue
        weight = float(raw_weight) * window_weight
        if weight <= 0:
            continue
        phrase_support[cand] += weight

        toks = plain_tokens(cand)
        if not toks:
            continue
        share = weight / max(len(toks), 1)
        for i, tok in enumerate(toks):
            token_support[tok] += share
            token_phrase_contexts[tok].add(cand)
            if i - 1 >= 0:
                token_neighbor_contexts[tok].add(toks[i - 1])
            if i + 1 < len(toks):
                token_neighbor_contexts[tok].add(toks[i + 1])
        if len(toks) >= 2:
            edge_share = weight / max(len(toks) - 1, 1)
            for a, b in zip(toks, toks[1:]):
                edge_support[(a, b)] += edge_share


def build_inventory(title: str, body: str, max_tags: int, verbose: bool = False):
    phrase_support: DefaultDict[str, float] = defaultdict(float)
    token_support: DefaultDict[str, float] = defaultdict(float)
    edge_support: DefaultDict[Tuple[str, str], float] = defaultdict(float)
    token_phrase_contexts: DefaultDict[str, set[str]] = defaultdict(set)
    token_neighbor_contexts: DefaultDict[str, set[str]] = defaultdict(set)
    candidate_set: set[str] = set()

    windows = build_windows(title, body)
    if verbose:
        print(f"[doc] windows={len(windows)}", file=sys.stderr)

    if _JIEBA_AVAILABLE and _jieba_analyse is not None:
        for text, w, kind in windows:
            tr = extract_textrank_candidates(text, top_k=max_tags * 4)
            tf = extract_tfidf_candidates(text, top_k=max_tags * 4)
            add_phrase_support(phrase_support, token_support, edge_support, token_phrase_contexts, token_neighbor_contexts, tr, w)
            add_phrase_support(phrase_support, token_support, edge_support, token_phrase_contexts, token_neighbor_contexts, tf, 0.9 * w)
            candidate_set.update(k for k in tr.keys() if not generic_shell_term(k))
            candidate_set.update(k for k in tf.keys() if not generic_shell_term(k))
            candidate_set.update(extract_local_noun_phrases(text))
            if verbose and kind in {"title", "lead"}:
                print(f"[doc] {kind} tr={list(tr.items())[:8]}", file=sys.stderr)
                print(f"[doc] {kind} tf={list(tf.items())[:8]}", file=sys.stderr)
    else:
        global _JIEBA_WARNED
        if not _JIEBA_WARNED:
            _JIEBA_WARNED = True
            sys.stderr.write(
                "NEXT: jieba is not installed; tags fall back to a lightweight extractor. "
                "For better Chinese tags, install jieba via 'pip install jieba'.\n"
            )
        merged = normalize_space(title + "\n" + body)
        for cand in extract_keywords_light(merged, max_k=max_tags * 8):
            if generic_shell_term(cand):
                continue
            phrase_support[cand] += 1.0
            toks = plain_tokens(cand)
            if toks:
                share = 1.0 / max(len(toks), 1)
                for i, tok in enumerate(toks):
                    token_support[tok] += share
                    token_phrase_contexts[tok].add(cand)
                    if i - 1 >= 0:
                        token_neighbor_contexts[tok].add(toks[i - 1])
                    if i + 1 < len(toks):
                        token_neighbor_contexts[tok].add(toks[i + 1])
                if len(toks) >= 2:
                    for a, b in zip(toks, toks[1:]):
                        edge_support[(a, b)] += 1.0 / max(len(toks) - 1, 1)
            candidate_set.add(cand)

    extras = extract_quoted_phrases(title + "\n" + body) + extract_ascii_phrases(title + "\n" + body)
    for cand in extras:
        if not generic_shell_term(cand):
            candidate_set.add(cand)

    return (
        windows,
        phrase_support,
        token_support,
        edge_support,
        token_phrase_contexts,
        token_neighbor_contexts,
        sorted(candidate_set),
    )


def count_occurrences(text: str, term: str) -> int:
    if not text or not term:
        return 0
    return len(re.findall(re.escape(term), text, flags=re.IGNORECASE))


def paragraph_spread(paragraphs: Sequence[str], term: str) -> int:
    low = term.lower()
    return sum(1 for p in paragraphs if low in p.lower())


def explicit_position_bonus(term: str, title: str, lead: str) -> float:
    score = 0.0
    if title and term.lower() in title.lower():
        score += 1.05
    if lead and term.lower() in lead.lower():
        score += 0.35
    return score


def length_shape_bonus(term: str) -> float:
    term = normalize_space(term)
    if not term:
        return 0.0
    if is_englishish(term):
        wc = len(term.split())
        if wc == 1:
            return 0.02
        if 2 <= wc <= 3:
            return 0.30
        if wc == 4:
            return 0.08
        return -0.25
    n = len(term)
    if 2 <= n <= 3:
        return 0.06
    if 4 <= n <= 10:
        return 0.38
    if 11 <= n <= 14:
        return 0.08
    return -0.22


def token_specificity_bonus(
    toks: Sequence[str],
    token_phrase_contexts: Dict[str, set[str]],
    token_neighbor_contexts: Dict[str, set[str]],
) -> float:
    if not toks:
        return 0.0
    vals = []
    for tok in toks:
        phrase_degree = len(token_phrase_contexts.get(tok, set()))
        neighbor_degree = len(token_neighbor_contexts.get(tok, set()))
        vals.append(1.0 / (1.0 + 0.24 * phrase_degree + 0.14 * neighbor_degree))
    return sum(vals) / len(vals)


def phrase_dominance_bonus(term: str, phrase_support: Dict[str, float]) -> float:
    term_n = normalize_space(term)
    if not term_n:
        return 0.0
    exact = phrase_support.get(term_n, 0.0)
    if exact <= 0:
        return 0.0
    contained_shorter = 0.0
    containing_longer = 0.0
    for other, val in phrase_support.items():
        if other == term_n or val <= 0:
            continue
        if other in term_n and len(other) < len(term_n):
            contained_shorter += val
        elif term_n in other and len(other) > len(term_n):
            containing_longer += val
    return math.log1p(exact) - 0.25 * math.log1p(contained_shorter) - 0.80 * math.log1p(containing_longer)


def phrase_family_penalty(term: str, phrase_support: Dict[str, float]) -> float:
    term_n = normalize_space(term)
    if not term_n:
        return 0.0
    count = 0
    for other in phrase_support.keys():
        if other == term_n:
            continue
        if term_n in other or other in term_n:
            count += 1
    return 0.10 * math.log1p(count)


def candidate_occurrence_density(paragraphs: Sequence[str], term: str) -> float:
    occs = [count_occurrences(p, term) for p in paragraphs if p]
    if not occs:
        return 0.0
    active = [x for x in occs if x > 0]
    if not active:
        return 0.0
    return sum(active) / len(active)


def context_dispersion_penalty(
    term: str,
    paragraphs: Sequence[str],
    token_phrase_contexts: Dict[str, set[str]],
    token_neighbor_contexts: Dict[str, set[str]],
) -> float:
    toks = plain_tokens(term)
    if not toks:
        return 0.0
    spread = paragraph_spread(paragraphs, term)
    total = max(len(paragraphs), 1)
    spread_ratio = spread / total
    phrase_degree = sum(len(token_phrase_contexts.get(t, set())) for t in toks) / len(toks)
    neighbor_degree = sum(len(token_neighbor_contexts.get(t, set())) for t in toks) / len(toks)
    if len(toks) >= 2:
        return 0.10 * max(spread_ratio - 0.70, 0.0) + 0.02 * max(phrase_degree - 12.0, 0.0)
    return 0.55 * max(spread_ratio - 0.45, 0.0) + 0.05 * max(phrase_degree - 10.0, 0.0) + 0.03 * max(neighbor_degree - 8.0, 0.0)


def candidate_quality_gate(term: str) -> bool:
    term = normalize_space(term)
    if not term or generic_shell_term(term):
        return False
    if not phrase_length_ok(term):
        return False
    if re.search(r"https?://|www\.", term, flags=re.I):
        return False
    if re.fullmatch(r"[_=+*/\-]{2,}", term):
        return False
    if re.search(r"(?:^|\s)(?:http|https|www|com|org|html?|md)(?:$|\s)", term, flags=re.I):
        return False

    toks = pos_tokens(term)
    if toks:
        flags = [f for _, f in toks]
        noun_ratio = sum(1 for f in flags if f in NOUNISH_POS) / max(len(flags), 1)
        if len(toks) == 1:
            word, flag = toks[0]
            if flag not in NOUNISH_POS and not is_englishish(word):
                return False
            if flag in {"v", "vd", "a", "ad", "d", "c", "p", "u", "xc"}:
                return False
        else:
            if noun_ratio < 0.52:
                return False
    else:
        if is_cjk(term) and len(term) >= 12:
            return False
        if is_englishish(term) and len(term.split()) >= 5:
            return False
    return True


def score_candidate(
    term: str,
    full_text: str,
    title: str,
    lead: str,
    paragraphs: Sequence[str],
    phrase_support: Dict[str, float],
    token_support: Dict[str, float],
    edge_support: Dict[Tuple[str, str], float],
    token_phrase_contexts: Dict[str, set[str]],
    token_neighbor_contexts: Dict[str, set[str]],
) -> float:
    term = normalize_space(term)
    if not (term and is_valid_token(term) and candidate_quality_gate(term)):
        return float("-inf")

    toks = plain_tokens(term)
    if not toks:
        return float("-inf")

    exact = phrase_support.get(term, 0.0)
    tok_vals = [token_support.get(t, 0.0) for t in toks]
    tok_avg = sum(tok_vals) / max(len(tok_vals), 1)
    tok_min = min(tok_vals) if tok_vals else 0.0

    edge_vals = [edge_support.get((a, b), 0.0) for a, b in zip(toks, toks[1:])]
    edge_avg = sum(edge_vals) / max(len(edge_vals), 1) if edge_vals else 0.0
    edge_min = min(edge_vals) if edge_vals else 0.0

    occ = count_occurrences(full_text, term)
    spread = paragraph_spread(paragraphs, term)
    density = candidate_occurrence_density(paragraphs, term)
    pos_bonus = explicit_position_bonus(term, title, lead)
    len_bonus = length_shape_bonus(term)
    penalty = sentence_like_penalty(term)

    specificity = token_specificity_bonus(toks, token_phrase_contexts, token_neighbor_contexts)
    dominance = phrase_dominance_bonus(term, phrase_support)
    family_pen = phrase_family_penalty(term, phrase_support)
    dispersion_pen = context_dispersion_penalty(term, paragraphs, token_phrase_contexts, token_neighbor_contexts)

    score = 0.0
    score += 1.30 * math.log1p(exact)
    score += 0.50 * math.log1p(tok_avg)
    score += 0.42 * math.log1p(tok_min)
    score += 1.05 * math.log1p(edge_avg)
    score += 0.78 * math.log1p(edge_min)
    score += 0.36 * math.log1p(occ)
    score += 0.36 * math.log1p(spread)
    score += 0.28 * math.log1p(density)
    score += 1.15 * specificity
    score += 0.95 * dominance
    score += pos_bonus
    score += len_bonus
    score -= penalty
    score -= family_pen
    score -= dispersion_pen

    if len(toks) >= 2:
        score += 1.00
        if exact > 0 and edge_min > 0:
            score += 0.45
        if exact > 0 and tok_min > 0 and edge_avg > 0:
            score += 0.35
        if title and term.lower() in title.lower():
            score += 0.22
    else:
        score -= 0.85
        if specificity < 0.62:
            score -= 0.70
        if exact <= 0 and spread <= 1:
            score -= 0.45
        if edge_avg <= 0:
            score -= 0.15

    if len(toks) >= 2 and exact >= max(tok_avg, 1.0):
        score += 0.35

    return score


def heuristic_tags(
    content: str,
    max_tags: int = 8,
    *,
    snippet_chars: int = 1200,
    verbose: bool = False,
) -> List[str]:
    text = content or ""
    title, body = get_title_and_body(text)
    lead = normalize_space(body[:snippet_chars]) if body else ""
    paragraphs = split_paragraphs(body)
    full_text = normalize_space(title + "\n" + body)

    (
        windows,
        phrase_support,
        token_support,
        edge_support,
        token_phrase_contexts,
        token_neighbor_contexts,
        base_candidates,
    ) = build_inventory(title, body, max_tags=max_tags, verbose=verbose)

    candidates: set[str] = set(base_candidates)
    if title:
        candidates.update(extract_local_noun_phrases(title))
        candidates.update(extract_textrank_candidates(title, top_k=max_tags * 3).keys())
        candidates.update(extract_tfidf_candidates(title, top_k=max_tags * 3).keys())

    if body:
        lead_text = lead
        candidates.update(extract_local_noun_phrases(lead_text))
        candidates.update(extract_textrank_candidates(lead_text, top_k=max_tags * 4).keys())
        candidates.update(extract_tfidf_candidates(lead_text, top_k=max_tags * 4).keys())

    if not candidates:
        candidates = set(extract_keywords_light(full_text, max_k=max_tags * 5))

    scores: Dict[str, float] = {}
    for cand in candidates:
        cand = normalize_space(cand)
        if not candidate_quality_gate(cand):
            continue
        score = score_candidate(
            cand,
            full_text=full_text,
            title=title,
            lead=lead,
            paragraphs=paragraphs,
            phrase_support=phrase_support,
            token_support=token_support,
            edge_support=edge_support,
            token_phrase_contexts=token_phrase_contexts,
            token_neighbor_contexts=token_neighbor_contexts,
        )
        if math.isfinite(score):
            scores[cand] = score

    ranked = sorted(scores.items(), key=lambda x: (-x[1], -len(x[0]), x[0].lower()))
    if verbose:
        print(f"[tags] candidate_count={len(candidates)}", file=sys.stderr)
        print(f"[tags] ranked preview={ranked[:30]}", file=sys.stderr)

    phrases = [(t, s) for t, s in ranked if len(plain_tokens(t)) >= 2]
    singles = [(t, s) for t, s in ranked if len(plain_tokens(t)) < 2]

    out: List[str] = []

    def try_add(term: str) -> bool:
        if generic_shell_term(term):
            return False
        if any(near_duplicate(term, chosen) for chosen in out):
            return False
        for chosen in out:
            if term.lower() in chosen.lower() and len(chosen) > len(term):
                return False
        out.append(term)
        return True

    phrase_target = min(max_tags, max(4, int(round(max_tags * 0.58))))
    for term, _score in phrases:
        if try_add(term) and len(out) >= phrase_target:
            break

    if len(out) < max_tags:
        best_score = ranked[0][1] if ranked else 0.0
        for term, score in singles:
            if score < best_score - 1.35:
                continue
            if try_add(term) and len(out) >= max_tags:
                break

    if len(out) < max_tags:
        for term, _score in ranked:
            if try_add(term) and len(out) >= max_tags:
                break

    if verbose:
        print(f"[tags] final tags={out}", file=sys.stderr)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone article tag extractor without embeddings."
    )
    parser.add_argument("filename", help="Markdown file path")
    parser.add_argument("-k", "--topk", type=int, default=10, help="Number of tags")
    parser.add_argument(
        "--snippet-chars",
        type=int,
        default=1200,
        help="How many leading body characters to use as lead context (default: 1200)",
    )
    parser.add_argument(
        "--no-md-clean",
        action="store_true",
        help="Disable markdown cleanup before extraction",
    )
    parser.add_argument(
        "--show-snippet",
        action="store_true",
        help="Print the extraction lead snippet to stderr for debugging",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print diagnostics to stderr",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.topk <= 0:
        print("Error: --topk must be positive", file=sys.stderr)
        return 1

    file_path = Path(args.filename)
    if not file_path.exists():
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        return 1

    raw = read_text(file_path)
    text = raw
    if not args.no_md_clean:
        text = clean_markdown(text)
    text = strip_metadata_for_generation(text)

    title, body = get_title_and_body(text)
    lead = normalize_space(body[:args.snippet_chars]) if body else ""
    if args.show_snippet:
        sys.stderr.write("----- TITLE -----\n")
        sys.stderr.write(title + "\n")
        sys.stderr.write("----- LEAD START -----\n")
        sys.stderr.write(lead + "\n")
        sys.stderr.write("----- LEAD END -----\n")

    tags = heuristic_tags(
        text,
        max_tags=args.topk,
        snippet_chars=args.snippet_chars,
        verbose=args.verbose,
    )
    print(tags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
