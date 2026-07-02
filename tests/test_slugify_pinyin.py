# -*- coding: utf-8 -*-
"""Tests for slugify() with pinyin-based filenames.

验证：
- 纯中文标题时，slugify 使用拼音生成 ASCII 友好的文件名；
- 中英混排标题时，保留英文单词并在前面加上拼音部分；
- 没有内容时回退到 'untitled'。
"""
from __future__ import annotations

import unittest
from unittest import mock

from clawsqlite_knowledge.utils import slugify


class SlugifyPinyinTests(unittest.TestCase):
    maxDiff = None

    def test_pure_chinese_title_to_pinyin_slug(self):
        title = "这是一个中文标题"
        slug = slugify(title, max_len=80)
        # pypinyin 默认会生成拼音片段，检查是否只包含 ascii/数字/连字符
        self.assertTrue(all(ord(c) < 128 for c in slug), msg=slug)
        self.assertIn("zhe", slug)
        self.assertIn("zhong", slug)

    def test_mixed_chinese_english_title(self):
        title = "想要搭建个人卫星地面站吗 Ground Station项目"
        slug = slugify(title, max_len=80)
        # 拼音 + 英文单词，全部 ascii
        self.assertTrue(all(ord(c) < 128 for c in slug), msg=slug)
        # Ground Station 这两个词应该出现在 slug 中
        self.assertIn("ground", slug)
        self.assertIn("station", slug)

    def test_mixed_chinese_english_title_without_pypinyin_keeps_ascii_words(self):
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "pypinyin":
                raise ImportError("pypinyin intentionally unavailable")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            slug = slugify("想要搭建个人卫星地面站吗 Ground Station项目", max_len=80)
        self.assertTrue(all(ord(c) < 128 for c in slug), msg=slug)
        self.assertIn("ground", slug)
        self.assertIn("station", slug)
        self.assertIn("xiang", slug)

    def test_empty_title_untitled(self):
        self.assertEqual(slugify(""), "untitled")
        self.assertEqual(slugify("   "), "untitled")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
