# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import unittest

from clawsqlite_knowledge import generator as genmod


class LLMGenerationTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._env = os.environ.copy()
        self._call_small_llm_json = genmod._call_small_llm_json

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        genmod._call_small_llm_json = self._call_small_llm_json

    def _enable_fake_llm(self) -> None:
        os.environ["SMALL_LLM_MODEL"] = "test-llm"
        os.environ["SMALL_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"
        os.environ["SMALL_LLM_API_KEY"] = "test-key"

    def test_llm_generation_uses_single_call_when_content_fits_context(self):
        self._enable_fake_llm()
        prompts: list[str] = []

        def fake_call(prompt: str, *, timeout: int = 60):
            prompts.append(prompt)
            return {
                "title": "Fitted title",
                "summary": "Fitted summary",
                "tags": ["sqlite", "agent", "knowledge", "config", "llm"],
                "key_claims": ["Uses one LLM call."],
                "entities": ["ClawSQLite"],
                "content_type": "note",
            }

        genmod._call_small_llm_json = fake_call
        fields = genmod.generate_fields(
            "short article body",
            hint_title=None,
            provider="llm",
            max_summary_chars=321,
            allow_heuristic=False,
            llm_context_window_chars=5000,
            llm_prompt_reserved_chars=1000,
        )

        self.assertEqual(fields["generation_quality"], "llm")
        self.assertEqual(fields["summary"], "Fitted summary")
        self.assertEqual(len(prompts), 1)
        self.assertIn("summary target length: about 321 characters", prompts[0])
        self.assertNotIn("Summarize one chunk", prompts[0])

    def test_llm_generation_chunks_when_content_exceeds_context_budget(self):
        self._enable_fake_llm()
        prompts: list[str] = []

        def fake_call(prompt: str, *, timeout: int = 60):
            prompts.append(prompt)
            if prompt.startswith("Summarize one chunk"):
                return {"summary": f"chunk summary {len(prompts)}"}
            return {
                "title": "Chunked title",
                "summary": "Chunked final summary",
                "tags": ["sqlite", "agent", "knowledge", "config", "chunking"],
                "key_claims": ["Chunks are synthesized."],
                "entities": ["ClawSQLite"],
                "content_type": "web_article",
            }

        genmod._call_small_llm_json = fake_call
        content = "x" * 2600
        fields = genmod.generate_fields(
            content,
            hint_title="Chunked",
            provider="llm",
            max_summary_chars=321,
            allow_heuristic=False,
            llm_context_window_chars=2200,
            llm_prompt_reserved_chars=1000,
            llm_chunk_overlap_chars=0,
        )

        chunk_prompts = [p for p in prompts if p.startswith("Summarize one chunk")]
        final_prompts = [p for p in prompts if "Input type: chunk summaries" in p]
        self.assertEqual(fields["generation_quality"], "llm")
        self.assertGreaterEqual(len(chunk_prompts), 2)
        self.assertEqual(len(final_prompts), 1)
        self.assertIn("Target summary length: about 300 characters", chunk_prompts[0])
        self.assertIn("summary target length: about 321 characters", final_prompts[0])

    def test_llm_required_raises_when_small_llm_env_is_missing(self):
        for key in ["SMALL_LLM_MODEL", "SMALL_LLM_BASE_URL", "SMALL_LLM_API_KEY"]:
            os.environ.pop(key, None)

        with self.assertRaises(RuntimeError):
            genmod.generate_fields(
                "content",
                hint_title="No LLM",
                provider="llm",
                allow_heuristic=False,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
