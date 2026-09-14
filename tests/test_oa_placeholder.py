"""测试 oa_reader 的微信 4.x 新模板 + 老模板兼容。

新模板：``#js_content`` 是 aria-hidden="true" 空 div，正文在
``<p style="line-height: 1.75em;">`` 段落里。
老模板：``#js_content`` 直接是正文。

只 mock 掉 HTTP 调用，不改 fetch_article_content 的内部逻辑。
"""
import re
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

from src.assistant.oa_reader import (
    _extract_line_height_paragraphs,
    fetch_article_content,
)


def _mock_resp(text: str) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.status_code = 200
    return r


class TestLineHeightExtractor(unittest.TestCase):
    """直接测 _extract_line_height_paragraphs（不发起 HTTP）"""

    def test_empty_html(self):
        self.assertEqual(_extract_line_height_paragraphs(""), "")

    def test_no_line_height_p(self):
        html = '<html><body><p>普通段落</p></body></html>'
        self.assertEqual(_extract_line_height_paragraphs(html), "")

    def test_typical_article(self):
        # 模拟微信 4.x 新模板里的 14 段正文（每段 ≥ 50 字符，过滤阈值是 30）
        html = "".join(
            f'<p style="line-height: 1.75em;"><span>{"段落" * (n+15)}</span></p>'
            for n in range(14)
        )
        out = _extract_line_height_paragraphs(html)
        # 14 段全部 ≥ 30 字符，全部保留
        self.assertEqual(out.count("\n\n") + 1, 14)
        # 拼接后总长 > 200
        self.assertGreater(len(out), 200)

    def test_filters_short_paragraphs(self):
        # 短段（< 30 字符）应被过滤
        html = (
            '<p style="line-height: 1.75em;"><span>短</span></p>'
            '<p style="line-height: 1.75em;"><span>' + ("长" * 50) + '</span></p>'
        )
        out = _extract_line_height_paragraphs(html)
        self.assertIn("长" * 50, out)
        self.assertNotIn("短", out)

    def test_handles_html_entities(self):
        # 段落里嵌 HTML 实体，清洗后应解开并保留（内容必须 ≥ 30 字符）
        long_entity_content = (
            "A&amp;B &nbsp; &lt;tag&gt; &quot;q&quot; " + "扩展填充 " * 10
        )
        html = (
            f'<p style="line-height: 1.75em;"><span>{long_entity_content}</span></p>'
        )
        out = _extract_line_height_paragraphs(html)
        self.assertIn("A&B", out)
        self.assertIn("<tag>", out)
        self.assertIn('"q"', out)
        # &nbsp; 应该被替换成空格
        self.assertNotIn("&nbsp;", out)


class TestOldTemplateArticle(unittest.TestCase):
    """老模板：#js_content 直接是正文，应该原样拿到。"""

    OLD_TEMPLATE_HTML = """<!DOCTYPE html>
<html><head><title>老模板测试</title></head>
<body>
<div id="js_content">
<p style="line-height: 1.75em;">短段</p>
<p>这是老模板的正文第一段，包含足够多的真实内容用于通过 30 字符阈值测试。</p>
<p>这是老模板的正文第二段，包含足够多的真实内容用于通过 30 字符阈值测试。</p>
</div>
<script>console.log("ad")</script>
</body></html>"""

    @patch("src.assistant.oa_reader.requests.get")
    def test_old_template_uses_js_content(self, mock_get):
        mock_get.return_value = _mock_resp(self.OLD_TEMPLATE_HTML)
        content = fetch_article_content("https://mp.weixin.qq.com/s/fake", timeout=5)
        # 老模板应该拿到 #js_content 里的正文
        self.assertIn("这是老模板的正文第一段", content)
        self.assertIn("这是老模板的正文第二段", content)
        # 短段不一定要包含（取决于清洗），但中文字符应 > 30
        self.assertGreater(len(content), 30)


class TestSharedFetch(unittest.TestCase):
    """Concurrent callers for one URL share only the active HTTP request."""

    @patch("src.assistant.oa_reader.requests.get")
    def test_same_url_overlapping_calls_share_http(self, mock_get):
        entered = threading.Event()
        release = threading.Event()
        html = TestOldTemplateArticle.OLD_TEMPLATE_HTML

        def get(_url, **_kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return _mock_resp(html)

        mock_get.side_effect = get
        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(
                    fetch_article_content("https://mp.weixin.qq.com/s/shared", timeout=3)
                )
            )
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(2))
        time.sleep(0.05)
        release.set()
        for thread in threads:
            thread.join(2)

        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertIn("这是老模板的正文第一段", results[0])

    @patch("src.assistant.oa_reader.requests.get")
    def test_different_urls_do_not_share_http(self, mock_get):
        mock_get.return_value = _mock_resp(TestOldTemplateArticle.OLD_TEMPLATE_HTML)

        first = fetch_article_content("https://mp.weixin.qq.com/s/one")
        second = fetch_article_content("https://mp.weixin.qq.com/s/two")

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(mock_get.call_count, 2)

    @patch("src.assistant.oa_reader.requests.get")
    def test_empty_fetch_releases_flight_for_retry(self, mock_get):
        from src.assistant import oa_reader
        mock_get.return_value = _mock_resp("<html><body></body></html>")

        self.assertEqual(
            fetch_article_content("https://mp.weixin.qq.com/s/empty-flight"), ""
        )
        self.assertEqual(oa_reader._fetch_flights, {})

        mock_get.return_value = _mock_resp(TestOldTemplateArticle.OLD_TEMPLATE_HTML)
        self.assertTrue(fetch_article_content("https://mp.weixin.qq.com/s/empty-flight"))
        self.assertEqual(mock_get.call_count, 2)

    @patch("src.assistant.oa_reader.requests.get")
    def test_failed_fetch_releases_flight_for_retry(self, mock_get):
        import requests
        mock_get.side_effect = [requests.Timeout("timeout"), _mock_resp(
            TestOldTemplateArticle.OLD_TEMPLATE_HTML
        )]

        self.assertEqual(
            fetch_article_content("https://mp.weixin.qq.com/s/retry"), ""
        )
        self.assertIn(
            "这是老模板的正文第一段",
            fetch_article_content("https://mp.weixin.qq.com/s/retry"),
        )
        self.assertEqual(mock_get.call_count, 2)


class TestNewTemplateArticle(unittest.TestCase):
    """新模板：#js_content 是 aria-hidden="true" 空 div，line-height p 拿正文。"""

    # 14 段，每段 ≥ 60 字符（贴近真实文章：4 段 + 多段补充），与生产环境
    # line-height 14 段总 1361 字符的特征一致。
    NEW_TEMPLATE_HTML = """<!DOCTYPE html>
<html><head><title>新模板测试</title></head>
<body>
<div class="original_panel_content" id="js_content" aria-hidden="true"></div>
<script>placeholder</script>
<p style="line-height: 1.75em;">
<span><span>恒生科技这几天又把很多人跌沉默了。9月10日收盘，恒生科技指数跌2.04%，报4330点，再一次逼近年内低点。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>把目光投向水面之下的资本大动作，你就明白大家在焦虑什么。前段时间，字节跳动在离岸市场大手笔贷款了296亿美元。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>在AI基础设施建设成为头部平台生死线的当下，谁先拿到便宜而充裕的资本，谁就多一分把模型跑通的底气。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>海底捞连续下挫，股价直接创下两年新低。这波大跌的诱因非常戏剧性，老板张勇的妻子以每股10.62港元套现。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>7月24日，财政部、税务总局发布了21号公告，明确规定离岸信托按20%缴纳个人所得税，并且给存量信托留了90天的补税窗口。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>许多在海外上市、采用离岸信托架构的企业家，都在面临同样的合规清算。中概和港股本来就两头受气。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>一边是科技巨头融资内卷AI带来的盈利焦虑，一边是老板们因为税收新规集中变现带来的情绪扰动。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>这恰恰勾勒出了当前中概互联板块最真实的生存现状，冰与火交织，压力与机遇并存。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>中概股今年已经跌了很长时间，整体估值本来就已经很便宜了，公司的基本面并没有发生恶化。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>对于长线投资者来说，可能是一个不错的买入机会。但入场一定要有足够的耐心，因为光靠便宜的估值很难实现强力反弹。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>接下来要想看到中概股重新迎来趋势性上涨，核心催化剂还是要看AI技术能否真正在应用端带来真金白银的收益。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>让市场看到实打实的商业回报，这才是中概互联板块走出低谷的根本路径，不能只靠情绪面的修复。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>从历史经验看，港股科技板块的底部往往伴随着情绪的极度悲观和资金的持续流出，而拐点则需要业绩的兑现来确认。</span></span>
</p>
<p style="line-height: 1.75em;">
<span><span>无论外部环境如何变化，AI产业革命的长期趋势不会改变，关键是找到能够在这一波浪潮中真正胜出的公司。</span></span>
</p>
<div class="original_panel_tool" aria-hidden="true">
  <span data-url="...">阅读全文</span>
</div>
</body></html>"""

    @patch("src.assistant.oa_reader.requests.get")
    def test_new_template_uses_line_height(self, mock_get):
        mock_get.return_value = _mock_resp(self.NEW_TEMPLATE_HTML)
        content = fetch_article_content("https://mp.weixin.qq.com/s/fake", timeout=5)
        # 必须拿到 line-height 里的正文
        self.assertIn("恒生科技", content)
        self.assertIn("把目光投向水面之下", content)
        # 拼接后总长 > 200（line-height 阈值）
        self.assertGreater(len(content), 200)
        # 不应该只是"阅读全文"
        self.assertNotEqual(content.strip(), "阅读全文")
        # 不应该包含 aria-hidden 元素
        self.assertNotIn("aria-hidden", content)


class TestEmptyTemplateArticle(unittest.TestCase):
    """极端情况：两个 div 都拿不到、line-height 也拿不到 → 返回空。"""

    EMPTY_HTML = """<!DOCTYPE html>
<html><body>
<div id="js_content" aria-hidden="true"></div>
<script></script>
</body></html>"""

    @patch("src.assistant.oa_reader.requests.get")
    def test_returns_empty_when_all_methods_fail(self, mock_get):
        mock_get.return_value = _mock_resp(self.EMPTY_HTML)
        content = fetch_article_content("https://mp.weixin.qq.com/s/fake", timeout=5)
        # 全部方法都拿不到 → 返回空字符串
        self.assertEqual(content, "")


class TestHttpFailure(unittest.TestCase):
    """HTTP 失败时返回空字符串，不抛异常。"""

    @patch("src.assistant.oa_reader.requests.get")
    def test_timeout_returns_empty(self, mock_get):
        import requests
        mock_get.side_effect = requests.Timeout("timeout")
        content = fetch_article_content("https://mp.weixin.qq.com/s/fake", timeout=5)
        self.assertEqual(content, "")


if __name__ == "__main__":
    unittest.main()
