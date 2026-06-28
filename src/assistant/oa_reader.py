"""
Official Account Article Reader — fetches full article content from URL

Scrapes WeChat article HTML pages and extracts main text content.
"""
import logging
import re
from html.parser import HTMLParser

import requests

logger = logging.getLogger(__name__)

# Suppress SSL warnings for WeChat CDN
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


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


def fetch_article_content(url: str, timeout: int = 15) -> str:
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
        logger.error("[OA-READER] Failed to fetch article: %s", e)
        return ""

    # Method 1: Regex extract #js_content
    m = re.search(r'id="js_content"[^>]*>(.*?)</div>\s*<script', html, re.DOTALL)
    if m:
        logger.debug("[OA-READER] Extraction method: regex #js_content")
    if not m:
        # Method 2: class="rich_media_content"
        m = re.search(
            r'class="rich_media_content[^"]*"[^>]*>(.*?)</div>\s*<script',
            html,
            re.DOTALL,
        )
        if m:
            logger.debug("[OA-READER] Extraction method: regex rich_media_content")

    if m:
        content_html = m.group(1)
    else:
        # Method 3: Use HTMLParser
        logger.debug("[OA-READER] Extraction method: HTMLParser fallback")
        extractor = WeChatArticleExtractor()
        try:
            extractor.feed(html)
        except Exception:
            pass
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
