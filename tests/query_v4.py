#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

try:
    import jieba
    import jieba.analyse as jieba_analyse
    _JIEBA_AVAILABLE = True
except Exception:
    jieba = None
    jieba_analyse = None
    _JIEBA_AVAILABLE = False

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_PUNCT_SPLIT_RE = re.compile(r"[\n。！？；;：:，,、（）()\[\]{}<>《》\"'“”‘’/\\|]+")
_VALID_CHARS_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff_.+\-/ ]+")


@dataclass(frozen=True)
class Token:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class Span:
    text: str
    unit_index: int
    start_token: int
    end_token: int
    start_char: int
    end_char: int
    tokens: Tuple[str, ...]


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def clean_query(text: str) -> str:
    text = text or ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n+", " ", text)
    return normalize_space(text)


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def is_englishish(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.+\-/ ]+", text or ""))


def is_valid_phrase(text: str) -> bool:
    text = normalize_space(text)
    if not text:
        return False
    if not _VALID_CHARS_RE.fullmatch(text):
        return False
    if re.fullmatch(r"\d+", text):
        return False
    if has_cjk(text):
        cjk_count = len(_CJK_RE.findall(text))
        if cjk_count < 2 or cjk_count > 20:
            return False
    elif is_englishish(text):
        words = [w for w in text.split() if w]
        if not words or len(words) > 5:
            return False
        if len(words) == 1 and len(words[0]) < 3:
            return False
    else:
        return False
    return True


def split_units_with_offsets(text: str) -> List[Tuple[str, int]]:
    units: List[Tuple[str, int]] = []
    pos = 0
    for part in _PUNCT_SPLIT_RE.split(text):
        if not part:
            continue
        idx = text.find(part, pos)
        if idx < 0:
            idx = pos
        unit = normalize_space(part)
        if unit:
            units.append((unit, idx))
        pos = idx + len(part)
    return units


def tokenize_unit(unit: str, unit_offset: int) -> List[Token]:
    tokens: List[Token] = []
    if _JIEBA_AVAILABLE and jieba is not None:
        pieces = [normalize_space(x) for x in jieba.lcut(unit, cut_all=False) if normalize_space(x)]
        cursor = 0
        for tok in pieces:
            if re.fullmatch(r"[\W_]+", tok):
                continue
            idx = unit.find(tok, cursor)
            if idx < 0:
                idx = unit.find(tok)
            if idx < 0:
                continue
            tokens.append(Token(tok, unit_offset + idx, unit_offset + idx + len(tok)))
            cursor = idx + len(tok)
        if tokens:
            return tokens

    for m in re.finditer(r"[A-Za-z0-9_.+\-/]+|[\u3400-\u9fff]{1,2}", unit):
        tok = m.group(0)
        if re.fullmatch(r"[\W_]+", tok):
            continue
        tokens.append(Token(tok, unit_offset + m.start(), unit_offset + m.end()))
    return tokens


def join_tokens(tokens: Sequence[str]) -> str:
    if not tokens:
        return ""
    if all(has_cjk(t) for t in tokens):
        return "".join(tokens)
    if all(is_englishish(t) and not has_cjk(t) for t in tokens):
        return " ".join(tokens)

    out: List[str] = []
    for tok in tokens:
        if not out:
            out.append(tok)
            continue
        prev = out[-1]
        if is_englishish(prev) and not has_cjk(prev) and is_englishish(tok) and not has_cjk(tok):
            out.append(" " + tok)
        else:
            out.append(tok)
    return normalize_space("".join(out))


def _get_idf_resources() -> Tuple[Dict[str, float], float]:
    if not _JIEBA_AVAILABLE or jieba_analyse is None:
        return {}, 1.0
    try:
        tfidf = jieba_analyse.TFIDF()
        idf_freq = getattr(tfidf, "idf_freq", {}) or {}
        median_idf = float(getattr(tfidf, "median_idf", 1.0) or 1.0)
        return dict(idf_freq), median_idf
    except Exception:
        return {}, 1.0


def _idf_value(token: str, idf_freq: Dict[str, float], median_idf: float) -> float:
    token = normalize_space(token)
    if not token:
        return 0.0
    value = idf_freq.get(token)
    if value is not None:
        return float(value)
    if has_cjk(token):
        return float(median_idf)
    return float(median_idf) * 0.85


def phrase_inventory(query: str) -> Tuple[Dict[str, float], Dict[str, float], Dict[Tuple[str, str], float], Dict[str, float]]:
    phrase_scores: Dict[str, float] = defaultdict(float)
    token_scores: Dict[str, float] = defaultdict(float)
    edge_scores: Dict[Tuple[str, str], float] = defaultdict(float)
    token_exact_scores: Dict[str, float] = defaultdict(float)

    if _JIEBA_AVAILABLE and jieba_analyse is not None:
        methods = []
        try:
            methods.append(jieba_analyse.extract_tags(query, topK=64, withWeight=True))
        except Exception:
            pass
        try:
            methods.append(jieba_analyse.textrank(query, topK=64, withWeight=True))
        except Exception:
            pass

        for items in methods:
            for raw_phrase, raw_weight in items:
                phrase = normalize_space(str(raw_phrase))
                if not phrase:
                    continue
                weight = float(raw_weight)
                phrase_scores[phrase] += weight
                toks = tokenize_unit(phrase, 0)
                tok_texts = [t.text for t in toks]
                if not tok_texts:
                    continue
                token_exact_scores[phrase] += weight
                share = weight / math.sqrt(len(tok_texts))
                for tok in tok_texts:
                    token_scores[tok] += share
                for a, b in zip(tok_texts, tok_texts[1:]):
                    edge_scores[(a, b)] += weight

    def _normalize_map(d: Dict) -> Dict:
        if not d:
            return dict(d)
        vals = list(d.values())
        hi = max(vals)
        if hi <= 0:
            return {k: 0.0 for k in d}
        return {k: float(v) / hi for k, v in d.items()}

    return (
        _normalize_map(dict(phrase_scores)),
        _normalize_map(dict(token_scores)),
        _normalize_map(dict(edge_scores)),
        _normalize_map(dict(token_exact_scores)),
    )


def token_information(query: str, tokens: Sequence[str]) -> Dict[str, float]:
    freq = Counter(tokens)
    idf_freq, median_idf = _get_idf_resources()
    phrase_scores, phrase_token_scores, _edge_scores, token_exact_scores = phrase_inventory(query)

    idf_raw: Dict[str, float] = {}
    for tok in freq:
        idf_raw[tok] = _idf_value(tok, idf_freq, median_idf)

    if idf_raw:
        hi = max(idf_raw.values())
        lo = min(idf_raw.values())
        if hi - lo < 1e-9:
            idf_norm = {k: 0.5 for k in idf_raw}
        else:
            idf_norm = {k: (v - lo) / (hi - lo) for k, v in idf_raw.items()}
    else:
        idf_norm = {}

    token_scores: Dict[str, float] = {}
    for tok, c in freq.items():
        length_bonus = math.log1p(len(tok)) / 3.0
        phrase_support = phrase_token_scores.get(tok, 0.0)
        exact_support = token_exact_scores.get(tok, 0.0)
        score = (
            0.52 * idf_norm.get(tok, 0.0)
            + 0.23 * phrase_support
            + 0.15 * exact_support
            + 0.08 * length_bonus
            + 0.02 * math.log1p(c)
        )
        token_scores[tok] = score

    if token_scores:
        hi = max(token_scores.values())
        if hi > 0:
            token_scores = {k: v / hi for k, v in token_scores.items()}

    return token_scores


def enumerate_spans(unit_index: int, tokens: Sequence[Token], max_span_tokens: int = 5) -> List[Span]:
    spans: List[Span] = []
    n = len(tokens)
    for i in range(n):
        for j in range(i, min(n, i + max_span_tokens)):
            piece_tokens = tuple(tok.text for tok in tokens[i : j + 1])
            text = join_tokens(piece_tokens)
            if not is_valid_phrase(text):
                continue
            spans.append(
                Span(
                    text=text,
                    unit_index=unit_index,
                    start_token=i,
                    end_token=j,
                    start_char=tokens[i].start,
                    end_char=tokens[j].end,
                    tokens=piece_tokens,
                )
            )
    return spans


def score_span(
    span: Span,
    all_unit_tokens: Sequence[Sequence[Token]],
    token_scores: Dict[str, float],
    phrase_scores: Dict[str, float],
    edge_scores: Dict[Tuple[str, str], float],
) -> float:
    weights = [token_scores.get(tok, 0.0) for tok in span.tokens]
    if not weights:
        return -1e9

    mean_w = sum(weights) / len(weights)
    min_w = min(weights)
    max_w = max(weights)
    exact_phrase = phrase_scores.get(span.text, 0.0)
    edge_vals = [edge_scores.get((a, b), 0.0) for a, b in zip(span.tokens, span.tokens[1:])]
    mean_edge = sum(edge_vals) / len(edge_vals) if edge_vals else 0.0
    min_edge = min(edge_vals) if edge_vals else 0.0

    unit_tokens = all_unit_tokens[span.unit_index]
    left_tok = unit_tokens[span.start_token - 1].text if span.start_token > 0 else None
    right_tok = unit_tokens[span.end_token + 1].text if span.end_token + 1 < len(unit_tokens) else None
    left_boundary = edge_scores.get((left_tok, span.tokens[0]), 0.0) if left_tok is not None else 0.0
    right_boundary = edge_scores.get((span.tokens[-1], right_tok), 0.0) if right_tok is not None else 0.0
    boundary_drop = max(0.0, mean_edge - left_boundary) + max(0.0, mean_edge - right_boundary)

    token_count = len(span.tokens)
    char_count = len(span.text.replace(" ", ""))
    compactness = math.log1p(char_count) / 3.0
    long_penalty = max(0, token_count - 3) * 0.14 + max(0, char_count - 12) * 0.02

    # Prefer spans whose every token is informative and whose internal edges are stronger
    # than the edges that connect them to outside context.
    score = 0.0
    score += 1.60 * mean_w
    score += 1.05 * min_w
    score += 0.25 * max_w
    score += 1.45 * exact_phrase
    score += 1.35 * mean_edge
    score += 0.70 * min_edge
    score += 0.80 * boundary_drop
    score += 0.18 * compactness
    score -= long_penalty

    # Single-token spans must rely more on intrinsic information, not just visibility.
    if token_count == 1:
        score += 0.45 * exact_phrase
        score -= 0.10 * max(0.0, 1.0 - mean_w)

    return score


def normalize_score_map(spans: Iterable[Span], raw_scores: Dict[Span, float]) -> Dict[Span, float]:
    vals = [raw_scores[s] for s in spans if math.isfinite(raw_scores[s])]
    if not vals:
        return raw_scores
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return {s: 0.0 for s in raw_scores}
    return {s: (v - lo) / (hi - lo) for s, v in raw_scores.items()}


def span_contains(a: Span, b: Span) -> bool:
    return a.start_char <= b.start_char and a.end_char >= b.end_char


def spans_overlap(a: Span, b: Span) -> bool:
    return not (a.end_char <= b.start_char or b.end_char <= a.start_char)


def near_duplicate_text(a: str, b: str) -> bool:
    al = normalize_space(a).lower()
    bl = normalize_space(b).lower()
    if not al or not bl:
        return False
    if al == bl or al in bl or bl in al:
        return True
    if has_cjk(al) and has_cjk(bl):
        sa = set(al)
        sb = set(bl)
        union = len(sa | sb)
        if union and len(sa & sb) / union >= 0.88:
            return True
    return False


def select_spans(spans: Sequence[Span], scores: Dict[Span, float], max_k: int) -> List[str]:
    ranked = sorted(spans, key=lambda s: (-scores[s], -len(s.text), s.text.lower()))
    if not ranked:
        return []

    top_score = scores[ranked[0]]
    adaptive_floor = max(0.55, top_score * 0.62)
    selected: List[Span] = []

    for span in ranked:
        current = scores[span]
        if selected and current < adaptive_floor:
            break

        keep = True
        for chosen in selected:
            if near_duplicate_text(span.text, chosen.text):
                keep = False
                break
            if span_contains(chosen, span) and scores[chosen] >= scores[span] - 0.06:
                keep = False
                break
            if span_contains(span, chosen) and scores[span] <= scores[chosen] + 0.09:
                keep = False
                break
            if spans_overlap(span, chosen):
                overlap = min(span.end_char, chosen.end_char) - max(span.start_char, chosen.start_char)
                smaller = min(span.end_char - span.start_char, chosen.end_char - chosen.start_char)
                if smaller > 0 and overlap / smaller >= 0.72 and scores[chosen] >= scores[span] - 0.04:
                    keep = False
                    break
        if keep:
            selected.append(span)
            if len(selected) >= max_k:
                break

    if not selected:
        return [ranked[0].text]
    return [s.text for s in selected]


def extract_query_keywords(query: str, max_k: int = 3, *, verbose: bool = False) -> List[str]:
    query = clean_query(query)
    if not query:
        return []

    units = split_units_with_offsets(query)
    all_unit_tokens: List[List[Token]] = []
    spans: List[Span] = []
    all_tokens: List[str] = []

    for unit_index, (unit, offset) in enumerate(units):
        toks = tokenize_unit(unit, offset)
        if not toks:
            continue
        all_unit_tokens.append(toks)
        all_tokens.extend(tok.text for tok in toks)
        spans.extend(enumerate_spans(len(all_unit_tokens) - 1, toks))

    if not spans:
        fallback = []
        for m in re.finditer(r"[A-Za-z][A-Za-z0-9_.+\-/]{2,31}|[\u3400-\u9fff]{2,10}", query):
            x = normalize_space(m.group(0))
            if is_valid_phrase(x) and x not in fallback:
                fallback.append(x)
        return fallback[:max_k]

    phrase_scores, phrase_token_scores, edge_scores, token_exact_scores = phrase_inventory(query)
    token_scores = token_information(query, all_tokens)

    # Re-inject phrase-derived token support after idf normalization.
    for tok, v in phrase_token_scores.items():
        token_scores[tok] = max(token_scores.get(tok, 0.0), 0.55 * token_scores.get(tok, 0.0) + 0.45 * v)
    for tok, v in token_exact_scores.items():
        if tok in token_scores:
            token_scores[tok] = max(token_scores[tok], 0.70 * token_scores[tok] + 0.30 * v)
    if token_scores:
        hi = max(token_scores.values())
        if hi > 0:
            token_scores = {k: v / hi for k, v in token_scores.items()}

    raw_scores = {
        span: score_span(span, all_unit_tokens, token_scores, phrase_scores, edge_scores)
        for span in spans
    }
    norm_scores = normalize_score_map(spans, raw_scores)
    outputs = select_spans(spans, norm_scores, max_k=max_k)

    if verbose:
        ranked = sorted(spans, key=lambda s: (-norm_scores[s], -len(s.text), s.text.lower()))[:25]
        print(f"[query] {query}", file=sys.stderr)
        print(f"[tokens] {[t for unit in all_unit_tokens for t in unit]}", file=sys.stderr)
        print(f"[token_scores] {sorted(token_scores.items(), key=lambda x: (-x[1], x[0]))}", file=sys.stderr)
        print(f"[phrase_scores] {sorted(phrase_scores.items(), key=lambda x: (-x[1], x[0]))[:20]}", file=sys.stderr)
        print("[top spans]", file=sys.stderr)
        for span in ranked:
            print(
                f"  {span.text!r} score={norm_scores[span]:.4f} tokens={span.tokens} chars=({span.start_char},{span.end_char})",
                file=sys.stderr,
            )
        print(f"[final] {outputs}", file=sys.stderr)

    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pure algorithmic query anchor extraction (v4).")
    parser.add_argument("query", nargs="?", default="", help="Natural-language query. If omitted, read from stdin.")
    parser.add_argument("-k", "--topk", type=int, default=3, help="Maximum number of anchors to output (default: 3)")
    parser.add_argument("--verbose", action="store_true", help="Print diagnostics to stderr")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    query = args.query.strip() if args.query else sys.stdin.read().strip()
    if not query:
        print("Error: empty query", file=sys.stderr)
        return 1
    if args.topk <= 0:
        print("Error: --topk must be positive", file=sys.stderr)
        return 1
    print(extract_query_keywords(query, max_k=args.topk, verbose=args.verbose))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
