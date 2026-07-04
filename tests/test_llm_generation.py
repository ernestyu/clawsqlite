# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest

from clawsqlite_knowledge import generator as genmod


class LLMGenerationTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._env = os.environ.copy()
        self._call_llm_json = genmod._call_llm_json

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        genmod._call_llm_json = self._call_llm_json

    def _enable_fake_llm(self) -> None:
        os.environ["LLM_MODEL"] = "test-llm"
        os.environ["LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
        os.environ["LLM_API_KEY"] = "test-key"

    def test_llm_generation_uses_single_call_when_content_fits_context(self):
        self._enable_fake_llm()
        prompts: list[str] = []

        def fake_call(prompt: str, *, timeout: int = 60):
            prompts.append(prompt)
            return {
                "title": "Fitted title",
                "summary": "Fitted summary",
                "tags": ["sqlite", "agent", "knowledge", "config", "llm", "ingest", "summary", "search"],
                "key_claims": ["Uses one LLM call."],
                "entities": ["ClawSQLite"],
                "category": "note",
                "content_type": "note",
            }

        genmod._call_llm_json = fake_call
        fields = genmod.generate_fields(
            "short article body",
            hint_title=None,
            provider="llm",
            max_summary_chars=321,
            allow_heuristic=False,
            source_kind="text",
            llm_context_window_tokens=4096,
        )

        self.assertEqual(fields["generation_quality"], "llm")
        self.assertEqual(fields["summary"], "short article body")
        self.assertEqual(len(prompts), 1)
        self.assertIn("summary target length: about 321 characters", prompts[0])
        self.assertIn("tags: exactly 8 short tags", prompts[0])
        self.assertIn("category and content_type must be identical", prompts[0])
        self.assertNotIn("Summarize one chunk", prompts[0])

    def test_llm_generation_chunks_head_tail_when_content_exceeds_context_budget(self):
        self._enable_fake_llm()
        prompts: list[str] = []

        def fake_call(prompt: str, *, timeout: int = 60):
            prompts.append(prompt)
            if prompt.startswith("Summarize one chunk"):
                return {"summary": f"chunk summary {len(prompts)}"}
            return {
                "title": "Chunked title",
                "summary": "Chunked final summary",
                "tags": ["sqlite", "agent", "knowledge", "config", "chunking", "summary", "search", "llm"],
                "key_claims": ["Chunks are synthesized."],
                "entities": ["ClawSQLite"],
                "category": "web_article",
                "content_type": "web_article",
            }

        genmod._call_llm_json = fake_call
        content = ("HEAD" * 1200) + ("MIDDLE" * 1200) + "TAIL_MARKER" + ("TAIL" * 500)
        fields = genmod.generate_fields(
            content,
            hint_title="Chunked",
            provider="llm",
            max_summary_chars=321,
            allow_heuristic=False,
            llm_context_window_tokens=2048,
            llm_max_chunks_per_article=2,
        )

        chunk_prompts = [p for p in prompts if p.startswith("Summarize one chunk")]
        final_prompts = [p for p in prompts if "Input type: chunk summaries" in p]
        self.assertEqual(fields["generation_quality"], "llm")
        self.assertEqual(len(chunk_prompts), 2)
        self.assertEqual(len(final_prompts), 1)
        self.assertIn("HEAD", chunk_prompts[0])
        self.assertIn("TAIL_MARKER", chunk_prompts[1])
        self.assertIn("Target summary length: about 300 characters", chunk_prompts[0])
        self.assertIn("summary target length: about 321 characters", final_prompts[0])

    def test_llm_generation_max_one_chunk_uses_single_partial_content_call(self):
        self._enable_fake_llm()
        prompts: list[str] = []

        def fake_call(prompt: str, *, timeout: int = 60):
            prompts.append(prompt)
            return {
                "title": "One chunk title",
                "summary": "One chunk summary",
                "tags": ["sqlite", "agent", "knowledge", "config", "chunking", "summary", "search", "llm"],
                "key_claims": ["Only the head chunk is used."],
                "entities": ["ClawSQLite"],
                "category": "web_article",
                "content_type": "web_article",
            }

        genmod._call_llm_json = fake_call
        fields = genmod.generate_fields(
            ("HEAD" * 2000) + "TAIL_MARKER",
            hint_title="One Chunk",
            provider="llm",
            max_summary_chars=321,
            allow_heuristic=False,
            llm_context_window_tokens=2048,
            llm_max_chunks_per_article=1,
        )

        self.assertEqual(fields["generation_quality"], "llm")
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("Summarize one chunk", prompts[0])
        self.assertIn("HEAD", prompts[0])
        self.assertNotIn("TAIL_MARKER", prompts[0])

    def test_llm_generation_rejects_generic_title(self):
        self._enable_fake_llm()

        def fake_call(prompt: str, *, timeout: int = 60):
            return {
                "title": "untitled",
                "summary": "Generated summary",
                "tags": ["sqlite", "agent", "knowledge", "config", "llm", "ingest", "summary", "search"],
                "key_claims": ["Reject generic title."],
                "entities": ["ClawSQLite"],
                "category": "note",
                "content_type": "note",
            }

        genmod._call_llm_json = fake_call
        with self.assertRaises(RuntimeError):
            genmod.generate_fields(
                "short article body",
                hint_title=None,
                provider="llm",
                allow_heuristic=False,
            )

    def test_llm_required_raises_when_llm_env_is_missing(self):
        for key in ["LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY"]:
            os.environ.pop(key, None)

        with self.assertRaises(RuntimeError):
            genmod.generate_fields(
                "content",
                hint_title="No LLM",
                provider="llm",
                allow_heuristic=False,
            )

    def test_short_web_article_does_not_passthrough_summary(self):
        self._enable_fake_llm()

        def fake_call(prompt: str, *, timeout: int = 60):
            return {
                "title": "Web title",
                "summary": "Generated web summary",
                "tags": ["sqlite", "agent", "knowledge", "config", "llm", "ingest", "summary", "search"],
                "key_claims": ["Web articles should be summarized."],
                "entities": ["ClawSQLite"],
                "category": "web_article",
                "content_type": "web_article",
            }

        genmod._call_llm_json = fake_call
        fields = genmod.generate_fields(
            "short web article body",
            hint_title=None,
            provider="llm",
            max_summary_chars=321,
            allow_heuristic=False,
            source_kind="url",
            source_content_type="web_article",
        )

        self.assertEqual(fields["summary"], "Generated web summary")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
