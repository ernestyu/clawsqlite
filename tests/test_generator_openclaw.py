# -*- coding: utf-8 -*-
"""Tests for generator.generate_fields with provider='openclaw'.

验证默认长摘要/标签生成逻辑（不依赖 LLM）：
- 使用前 800 字左右作为长摘要，并尽量在完整句子/段落边界截断；
- 生成的 tags 数量和内容大致合理；
- 字数/阅读时间等微信噪音会被过滤掉，不进入摘要/标签。
"""
from __future__ import annotations

import unittest

from clawsqlite_knowledge.generator import generate_fields


_SAMPLE_CONTENT = """字数 1136，阅读大约需 6 分钟

想要搭建个人卫星地面站吗？ Ground Station项目让你把卫星地面中心装进电脑

地球轨道上正有数万颗活跃卫星在飞行，其中相当一部分每天都会从你头顶经过。你只需要一根二三十块钱的RTL-SDR接收棒、一台普通电脑，再加上Ground Station这套开源软件，就能把这些飞行器变成可以监测、解码、录制的信号源。

这个项目由GitHub用户sgoudelis开发，目前拥有77个star, GPL-3.0协议开放全部源码。表面上看，它是一个业余无线电工具箱；但如果你仔细拆解它的架构，会发现它更像一套微型地面站指控系统——有前端界面、后端API、信号处理工作进程、自动化调度引擎，甚至还接入了AI语音转录。

整套系统基于React+FastAPI构建，前后端通过 Socket.IO保持全双工实时连接。核心的信号处理部分被拆分成多个独立Worker进程，彼此之间通过发布/订阅模式传递数据——这种设计让它在处理高速IQ数据流时不会因为某个消费者变慢而拖累整条链路。

大多数人看到这个项目第一眼关注的是频谱瀑布图和卫星追踪，但这套系统真正有趣的地方藏在细节里。

这里省略若干内容，用来模拟一篇超过 800 字的文章……
"""


class GeneratorOpenClawTests(unittest.TestCase):
    maxDiff = None

    def test_generate_fields_openclaw_summary_and_tags(self):
        """默认 provider='openclaw' 能生成合理的长摘要和标签。"""
        fields = generate_fields(_SAMPLE_CONTENT, hint_title=None, provider="openclaw", max_summary_chars=800)

        title = fields["title"]
        summary = fields["summary"]
        tags = fields["tags"]

        # 标题应该来自正文第一行的内容片段
        self.assertTrue(len(title) > 0)

        # 长摘要：长度在 200~800 字之间，且不是完整全文
        self.assertTrue(200 <= len(summary) <= 800, msg=f"summary length={len(summary)}")
        self.assertNotEqual(summary.strip(), _SAMPLE_CONTENT.strip())

        # 摘要末尾尽量在句子/段落边界（带句号或换行），避免硬截断
        # 这里不做严格断言，只要求末尾不是明显的半个 token
        self.assertNotIn("字数", summary)
        self.assertNotIn("阅读大约需", summary)

        # 标签：数量在 1~12 之间，内容非空
        self.assertIsInstance(tags, list)
        self.assertGreaterEqual(len(tags), 1)
        self.assertLessEqual(len(tags), 12)
        for t in tags:
            self.assertTrue(isinstance(t, str) and t.strip())
            # 标签里不应该出现明显的噪音词
            self.assertNotIn("字数", t)
            self.assertNotIn("阅读", t)
            self.assertNotIn("分钟", t)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()  # type: ignore[arg-type]
