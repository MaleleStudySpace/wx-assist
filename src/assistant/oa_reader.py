"""
Official Account Article Reader — fetches full article content from URL

Scrapes WeChat article HTML pages and extracts main text content.

兼容两种微信文章模板：
- 老模板（多数公众号）：正文在 ``<div id="js_content">`` 里，Method 1 直接拿到
- 新模板（WeChat 4.x 部分文章）：``#js_content`` 是 aria-hidden="true" 空 div 占位
  （"点击查看原文" 的客户端渲染占位），正文在散落的
  ``<p style="line-height: 1.75em;">`` 段落里 —— Method 1 拿到 0 字符时降级到
  line-height 段落提取

为保证对老模板文章零影响，新分支只在 Method 1/2 捕获内容过短或容器标记为 ``aria-hidden`` 时介入。
"""
import logging
import re
import threading
from html.parser import HTMLParser

import requests

logger = logging.getLogger(__name__)

# Suppress SSL warnings for WeChat CDN
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# Method 1/2 命中后判定"空占位"的阈值：< 30 字符视为 aria-hidden 占位 div
# 老模板正文远大于此值（实测 1000-12000 字符），新模板空 div 是 0 字符
_EMPTY_CONTENT_THRESHOLD = 30

# line-height 提取最少字符阈值：低于此值视为失败，继续走 Method 3/4
_LINE_HEIGHT_MIN_CHARS = 200

# line-height 段落过滤：短于 30 字符的视为菜单/分隔符/占位文字
_LINE_HEIGHT_PARA_MIN_CHARS = 30


class _FetchFlight:
    """State for one in-progress article fetch shared by URL."""

    def __init__(self):
        self.done = threading.Event()
        self.result = ""
        self.error: BaseException | None = None


# Only coalesce requests that overlap in time.  Results are deliberately not
# retained after the owner finishes, so a later retry can recover from a
# transient network failure.
_fetch_flights: dict[str, _FetchFlight] = {}
_fetch_flights_lock = threading.Lock()


class WeChatArticleExtractor(HTMLParser):
    """Extract article body text from WeChat HTML pages."""

    def __init__(self):
        super().__init__()
        self.in_content = False
        self.sections = []
        self.current_text = []
        self.tag_stack = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        id_val = attrs_dict.get("id", "")

        # WeChat article body container
        if id_val == "js_content" or "rich_media_content" in cls:
            self.in_content = True

        # Heading tags
        if tag in ("h1", "h2", "h3", "h4"):
            self.tag_stack.append(tag)

        # Image alt text
        if tag == "img":
            alt = attrs_dict.get("alt", "")
            if alt and self.in_content:
                self.current_text.append(f"[img: {alt}]")

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4") and self.tag_stack and self.tag_stack[-1] == tag:
            self.tag_stack.pop()
            text = " ".join(self.current_text).strip()
            if text:
                self.sections.append({"type": "heading", "text": text})
            self.current_text = []
        if tag in ("p", "section"):
            text = " ".join(self.current_text).strip()
            if text and self.in_content:
                self.sections.append({"type": "paragraph", "text": text})
            self.current_text = []

    def handle_data(self, data):
        if self.in_content:
            text = data.strip()
            if text:
                self.current_text.append(text)

    def get_content(self) -> list[dict]:
        return self.sections


def _extract_line_height_paragraphs(html: str) -> str:
    """从微信 4.x 新模板里提取正文。

    新模板的真实正文散落在 ``<p style="line-height: 1.75em;">`` 这类段落里
    （``#js_content`` 只是 aria-hidden="true" 的占位 div）。

    策略：
    1. 匹配所有带 line-height 样式的 ``<p>`` 段落
    2. 清洗 HTML 标签与 HTML 实体
    3. 过滤掉 < 30 字符的短段（菜单、按钮文字、占位"阅读全文"等）
    4. 用 ``\\n\\n`` 拼接保留段落分隔

    Returns:
        拼接后的正文（可能为空字符串——让调用方走其他降级路径）
    """
    paragraphs = re.findall(
        r'<p[^>]*style="[^"]*line-height:[^"]*"[^>]*>(.*?)</p>',
        html, re.DOTALL,
    )
    if not paragraphs:
        return ""
    cleaned: list[str] = []
    for p in paragraphs:
        text = re.sub(r"<[^>]+>", "", p)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&quot;", '"', text)
        text = re.sub(r"&#39;", "'", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) >= _LINE_HEIGHT_PARA_MIN_CHARS:
            cleaned.append(text)
    return "\n\n".join(cleaned)


def _fetch_article_content_uncached(url: str, timeout: int = 15, title: str = "") -> str:
    """Fetch a WeChat article and extract its main text content.

    Args:
        url: Article URL (mp.weixin.qq.com)
        timeout: Request timeout in seconds

    Returns:
        Extracted text content
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout, verify=False)
        resp.encoding = "utf-8"
        html = resp.text
        logger.debug("[OA-READER] Fetch %s: status=%d, html_len=%d", url[:80], resp.status_code, len(html))
    except Exception as e:
        logger.error("[OA-READER] Failed to fetch article「%s」: %s", title or url[:60], e)
        return ""

    # Method 1: Regex extract #js_content
    # 用 lookahead (?=\s*<script) 确保匹配的是 js_content 自身的关闭 div，
    # 而不是页面其他位置更后面的 </div><script> —— 微信 4.x 新模板的
    # #js_content 是个空 div（aria-hidden="true"），真实正文在
    # <p style="line-height:..."> 段落里，老模板的 #js_content 是真正的正文容器。
    m = re.search(
        r'<div[^>]*\bid="js_content"[^>]*>(.*?)</div>(?=\s*<script)',
        html, re.DOTALL,
    )
    if m:
        captured = m.group(1).strip()
        # 新模板判定：捕获内容空 / < 30 字符 / 整个 div 自身带 aria-hidden="true"
        # 三个条件任一为真都视为占位 div，让 m=None 走降级
        full_div = m.group(0)
        is_aria_hidden = 'aria-hidden="true"' in full_div
        if len(captured) < _EMPTY_CONTENT_THRESHOLD or is_aria_hidden:
            logger.debug(
                "[OA-READER] js_content placeholder div (captured=%d chars, aria-hidden=%s), trying line-height p",
                len(captured), is_aria_hidden,
            )
            m = None
        else:
            logger.debug("[OA-READER] Extraction method: regex #js_content (%d chars)", len(captured))
    if not m:
        # Method 2: class="rich_media_content"
        m = re.search(
            r'<div[^>]*class="rich_media_content[^"]*"[^>]*>(.*?)</div>(?=\s*<script)',
            html, re.DOTALL,
        )
        if m:
            captured = m.group(1).strip()
            full_div = m.group(0)
            is_aria_hidden = 'aria-hidden="true"' in full_div
            if len(captured) < _EMPTY_CONTENT_THRESHOLD or is_aria_hidden:
                logger.debug(
                    "[OA-READER] rich_media_content placeholder div (captured=%d chars, aria-hidden=%s), trying line-height p",
                    len(captured), is_aria_hidden,
                )
                m = None
            else:
                logger.debug("[OA-READER] Extraction method: regex rich_media_content (%d chars)", len(captured))

    # Method 0 (微信 4.x 新模板兜底)：Method 1/2 命中但只是空占位 div 时，
    # 从 <p style="line-height:..."> 段落里拼正文。**仅在 m=None 时触发**，
    # 对老模板（Method 1/2 拿到真实正文）零影响。
    if not m:
        line_height_text = _extract_line_height_paragraphs(html)
        if line_height_text and len(line_height_text) >= _LINE_HEIGHT_MIN_CHARS:
            logger.info(
                "[OA-READER] line-height p extracted %d chars for %s",
                len(line_height_text), url[:80],
            )
            return line_height_text
        logger.debug(
            "[OA-READER] line-height p 不达标 (%d chars < %d)，继续走 Method 3/4",
            len(line_height_text) if line_height_text else 0, _LINE_HEIGHT_MIN_CHARS,
        )

    if m:
        content_html = m.group(1)
    else:
        # Method 3: Use HTMLParser
        logger.debug("[OA-READER] Extraction method: HTMLParser fallback")
        extractor = WeChatArticleExtractor()
        try:
            extractor.feed(html)
        except Exception as e:
            # 核心兜底解析器失败 → 用户拿到质量降级的 <p> 文本或空内容，
            # 与"正文就是短"无法区分。debug 级（抓取链路较频，防刷屏）。
            logger.debug("[OA-READER] HTMLParser feed 失败，将走 <p> 兜底: %s", e)
        sections = extractor.get_content()
        if sections:
            return "\n\n".join(
                f"{'## ' if s['type'] == 'heading' else ''}{s['text']}"
                for s in sections
            )
        # Last resort: extract <p> tags
        logger.debug("[OA-READER] Extraction method: <p> tag fallback")
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
        content_html = "\n".join(paragraphs)

    # Clean HTML tags
    text = re.sub(r"<br\s*/?>", "\n", content_html)
    text = re.sub(r'<img[^>]*alt="([^"]*)"[^>]*>', r"[img: \1]", text)
    text = re.sub(r"<img[^>]*>", "[img]", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    result = text.strip()
    if not result:
        logger.warning("[OA-READER] All extraction methods returned empty for %s", url[:80])
    else:
        logger.debug("[OA-READER] Extracted %d chars from %s", len(result), url[:80])
    return result


def fetch_article_content(url: str, timeout: int = 15, title: str = "") -> str:
    """Fetch an article, sharing an overlapping request for the same URL.

    The request is coalesced only while it is in flight.  Its result is not
    cached here: callers may retry later after a transient failure, while the
    durable ``oa_cache`` remains the source of truth across restarts.
    """
    key = (url or "").strip()
    if not key:
        return ""

    with _fetch_flights_lock:
        flight = _fetch_flights.get(key)
        owner = flight is None
        if owner:
            flight = _FetchFlight()
            _fetch_flights[key] = flight

    if not owner:
        # Do not start a second request when the shared owner is slow.  The
        # caller's own timeout bounds how long they are willing to wait.
        wait_timeout = max(0.1, float(timeout or 15))
        if not flight.done.wait(wait_timeout):
            logger.debug("[OA-READER] Shared fetch wait timed out for %s", key[:80])
            return ""
        return flight.result

    try:
        flight.result = _fetch_article_content_uncached(key, timeout=timeout, title=title)
    except Exception as e:
        # Preserve the existing best-effort contract for unexpected parser
        # failures: callers receive an empty result, and waiters are released.
        flight.error = e
        logger.error("[OA-READER] Shared fetch failed for「%s」: %s", title or key[:60], e)
    finally:
        # Signal before removing the entry.  A caller arriving in this small
        # window can safely consume the completed result instead of starting
        # a duplicate request.
        flight.done.set()
        with _fetch_flights_lock:
            if _fetch_flights.get(key) is flight:
                del _fetch_flights[key]

    if flight.error is not None:
        raise flight.error
    return flight.result
