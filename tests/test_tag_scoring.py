# -*- coding: utf-8 -*-
"""Tests for tag scoring and search weight parsing."""
from __future__ import annotations

import os
import unittest

from clawsqlite_knowledge.generator import generate_keywords_for_search, generate_search_query_plan
from clawsqlite_knowledge.search import (
    _DEFAULT_SCORE_WEIGHTS,
    _DEFAULT_TEXT_SCORE_WEIGHTS,
    _score_weights_for_mode,
    _score_weights_from_env,
)
from clawsqlite_knowledge.utils import tag_match_score


class TagScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_tag_match_score_basic(self):
        tags = "alpha,beta,gamma"
        self.assertAlmostEqual(tag_match_score(["alpha"], tags), 1.0, places=6)
        self.assertAlmostEqual(tag_match_score(["beta"], tags), 0.5, places=6)
        score = tag_match_score(["alpha", "gamma"], tags)
        self.assertAlmostEqual(score, (1.0 + 1.0 / 3.0) / (1.0 + 1.0 / 2.0), places=6)

    def test_tag_match_score_handles_chinese_comma(self):
        tags = "alpha，beta，gamma"
        self.assertAlmostEqual(tag_match_score(["beta"], tags), 0.5, places=6)

    def test_tag_match_score_no_match(self):
        self.assertEqual(tag_match_score(["zzz"], "alpha,beta"), 0.0)

    def test_score_weights_env_normalize(self):
        os.environ["CLAWSQLITE_SCORE_WEIGHTS"] = "vec=1,fts=1,tag=1,priority=1,recency=1"
        weights = _score_weights_from_env()
        self.assertEqual(set(weights.keys()), set(_DEFAULT_SCORE_WEIGHTS.keys()))
        for v in weights.values():
            self.assertAlmostEqual(v, 0.2, places=6)

    def test_score_weights_env_partial_ignored(self):
        os.environ["CLAWSQLITE_SCORE_WEIGHTS"] = "vec=0.5,fts=0.5"
        self.assertEqual(_score_weights_from_env(), _DEFAULT_SCORE_WEIGHTS)

    def test_score_weights_text_mode_env_normalize(self):
        os.environ["CLAWSQLITE_SCORE_WEIGHTS_TEXT"] = "fts=1,tag=1,priority=1,recency=1"
        weights = _score_weights_for_mode("mode2")
        self.assertEqual(set(weights.keys()), set(_DEFAULT_TEXT_SCORE_WEIGHTS.keys()))
        for v in weights.values():
            self.assertAlmostEqual(v, 0.25, places=6)

    def test_score_weights_mode_specific_override(self):
        os.environ["CLAWSQLITE_SCORE_WEIGHTS_MODE4"] = "fts=2,tag=1,priority=1,recency=0"
        weights = _score_weights_for_mode("mode4")
        self.assertAlmostEqual(weights["fts"], 0.5, places=6)
        self.assertAlmostEqual(weights["tag"], 0.25, places=6)
        self.assertAlmostEqual(weights["priority"], 0.25, places=6)
        self.assertAlmostEqual(weights["recency"], 0.0, places=6)

    def test_generate_keywords_for_search_openclaw_ascii(self):
        kws = generate_keywords_for_search("hello world hello", provider="openclaw", max_k=10)
        self.assertEqual(kws, ["hello", "world"])

    def test_generate_keywords_for_search_empty(self):
        self.assertEqual(generate_keywords_for_search("", provider="openclaw", max_k=10), [])

    def test_generate_keywords_for_search_llm_fallback(self):
        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop("LLM_API_KEY", None)
        kws = generate_keywords_for_search("alpha beta", provider="llm", max_k=10)
        self.assertEqual(kws, ["alpha", "beta"])

    def test_generate_search_query_plan_openclaw(self):
        out = generate_search_query_plan("alpha beta alpha", provider="openclaw", max_k=10, min_k=3)
        self.assertEqual(out["query_refine"], "alpha beta alpha")
        self.assertEqual(out["query_tags"], ["alpha", "beta"])
        self.assertFalse(out["used_llm"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
