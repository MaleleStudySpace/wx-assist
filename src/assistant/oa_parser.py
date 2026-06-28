"""
Official Account (公众号) Article Parser

Handles:
- zstd decompression of message content
- XML parsing for Type 49 (appmsg) messages
- Extraction of article metadata (title, url, digest, source)
"""
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False
    logger.warning("zstandard not installed — OA article decompression will fail")

ZSTD_MAGIC = bytes([0x28, 0xB5, 0x2F, 0xFD])


@dataclass
class OAArticle:
    """Represents a parsed Official Account article."""

    title: str = ""
    url: str = ""
    digest: str = ""
    cover: str = ""
    source_name: str = ""
    source_username: str = ""
    pub_time: int = 0
    gh_id: str = ""
    timestamp: int = 0  # Message create_time

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "digest": self.digest,
            "cover": self.cover,
            "source_name": self.source_name,
            "source_username": self.source_username,
            "pub_time": self.pub_time,
            "gh_id": self.gh_id,
            "timestamp": self.timestamp,
        }


def decode_content(content_hex: str) -> str:
    """Decode message_content field, supporting zstd compression and plain text.

    Args:
        content_hex: Hex-encoded content string from WCDB

    Returns:
        Decompressed text (usually XML)
    """
    if not content_hex:
        return ""

    # Try hex decode
    try:
        raw = bytes.fromhex(content_hex)
    except ValueError:
        # Not hex — plain text
        return content_hex

    # Check zstd magic number
    if raw[:4] == ZSTD_MAGIC:
        if not HAS_ZSTD:
            logger.error("zstd content found but zstandard library not installed")
            return ""
        try:
            dctx = zstd.ZstdDecompressor()
            return dctx.decompress(raw).decode("utf-8", errors="replace")
        except Exception as e:
            logger.error(f"zstd decompression failed: {e}")
            return ""

    # Not zstd — try as UTF-8 text
    try:
        return raw.decode("utf-8")
    except Exception:
        return content_hex


def extract_xml(xml: str, tag: str) -> str:
    """Extract tag content from XML, supporting CDATA format.

    Handles both:
    - <title>plain text</title>
    - <title><![CDATA[text]]></title>
    """
    pattern = f"<{tag}>(?:<!\\[CDATA\\[)?(.*?)(?:\\]\\]>)?</{tag}>"
    m = re.search(pattern, xml, re.DOTALL)
    return m.group(1).strip() if m else ""


def parse_oa_article(xml: str) -> dict:
    """Parse Type 49 OA message XML into structured article data.

    Returns:
        dict with keys: title, des, url, source_name, source_username, articles[]
    """
    result = {
        "title": extract_xml(xml, "title"),
        "des": extract_xml(xml, "des") or extract_xml(xml, "description"),
        "url": extract_xml(xml, "url"),
        "xml_type": extract_xml(xml, "type"),
        "cover": "",
        "source_name": "",
        "source_username": extract_xml(xml, "sourceusername"),
        "articles": [],
    }

    # Cover image: multiple sources by priority
    result["cover"] = (
        extract_xml(xml, "thumburl")
        or extract_xml(xml, "cdnthumburl")
        or extract_xml(xml, "cover")
        or extract_xml(xml, "cover_235_1")
    )

    # Source name from <mmreader><category><name>
    category_match = re.search(
        r"<category[^>]*>(.*?)</category>", xml, re.DOTALL
    )
    if category_match:
        cat_xml = category_match.group(1)
        result["source_name"] = extract_xml(cat_xml, "name")

        # Extract <item> list (multi-article push)
        items = re.findall(r"<item>(.*?)</item>", cat_xml, re.DOTALL)
        if items:
            for item_xml in items:
                art = {
                    "title": extract_xml(item_xml, "title"),
                    "url": extract_xml(item_xml, "url"),
                    "digest": (
                        extract_xml(item_xml, "summary")
                        or extract_xml(item_xml, "digest")
                    ),
                    "cover": extract_xml(item_xml, "cover"),
                    "pub_time": extract_xml(item_xml, "pub_time"),
                    "source_name": result["source_name"],
                }
                if art["title"] and art["url"]:
                    result["articles"].append(art)

    # Fallback: if no mmreader/item, use top-level fields as single article
    if not result["articles"] and result["title"] and result["url"]:
        result["articles"].append({
            "title": result["title"],
            "url": result["url"],
            "digest": result["des"],
            "cover": result["cover"],
            "pub_time": "",
            "source_name": result["source_name"],
        })

    return result


def fetch_oa_articles(client, gh_id: str, limit: int = 20) -> list[OAArticle]:
    """Fetch all articles from a specific Official Account.

    Args:
        client: WcdbNativeClient instance
        gh_id: Official Account ID (e.g., "gh_010999ea1270")
        limit: Max messages to fetch

    Returns:
        List of OAArticle objects
    """
    msgs = client.get_messages(talker=gh_id, limit=limit)
    all_articles = []

    for m in msgs:
        # Parse message type (strip high bits)
        lt = int(m.get("local_type", 0))
        real_type = lt & 0xFFFF

        if real_type != 49:  # Only process Type 49 (appmsg)
            continue

        # Decompress content
        content_hex = m.get("message_content", "")
        xml = decode_content(content_hex)
        if "<appmsg" not in xml:
            continue

        # Parse article
        parsed = parse_oa_article(xml)
        ts = int(m.get("create_time", 0) or 0)

        for art in parsed["articles"]:
            article = OAArticle(
                title=art.get("title", ""),
                url=art.get("url", ""),
                digest=art.get("digest", ""),
                cover=art.get("cover", ""),
                source_name=art.get("source_name", "") or parsed.get("source_name", ""),
                source_username=parsed.get("source_username", ""),
                pub_time=int(art.get("pub_time", 0) or 0),
                gh_id=gh_id,
                timestamp=ts,
            )
            all_articles.append(article)

    return all_articles


def get_oa_sessions(client) -> list[dict]:
    """Get all Official Account (公众号) sessions, excluding service accounts (服务号).

    Distinguishes between:
    - 订阅号/公众号: verify_flag bit 3 (8) set, bit 4 (16) NOT set
    - 服务号: verify_flag bit 4 (16) set (e.g. 微信支付, 信用卡还款)

    Service accounts (verify_flag & 16 != 0) are excluded because they are
    payment/notification accounts, not content publishers.

    Returns:
        List of session dicts for real 公众号 only
    """
    sessions = client.get_sessions(limit=500)
    gh_sessions = [s for s in sessions if s.get("username", "").startswith("gh_")]

    if not gh_sessions:
        return []

    # Query contact table to get verify_flag for each gh_ account
    service_gh_ids = _get_service_account_ids(client, gh_sessions)
    if not service_gh_ids:
        return gh_sessions

    # Filter out service accounts
    return [s for s in gh_sessions if s.get("username", "") not in service_gh_ids]


# Cache for service account flags (avoids repeated DB queries)
_service_ids_cache: dict[str, bool] = {}


def _get_service_account_ids(client, gh_sessions: list[dict]) -> set[str]:
    """Identify service accounts (服务号) using biz_info.type first, fallback to verify_flag.

    biz_info.type=1 → service account (微信支付, 信用卡还款 etc.)
    biz_info.type=0 → public account (公众号/订阅号)
    verify_flag & 16 is unreliable (公众号 like 新智元 also has bit 4 set).
    """
    gh_ids = [s.get("username", "") for s in gh_sessions if s.get("username", "")]
    if not gh_ids:
        return set()

    service_ids = {gid for gid in gh_ids if _service_ids_cache.get(gid) is True}
    missing_ids = [gid for gid in gh_ids if gid not in _service_ids_cache]
    if not missing_ids:
        return service_ids

    # 优先用 biz_info.type 字段（精确区分服务号和公众号）
    try:
        quoted = ",".join(f"'{gid}'" for gid in missing_ids)
        sql = f"SELECT username, type FROM biz_info WHERE username IN ({quoted})"
        rows = client.exec_query("contact", "", sql)
        if rows:
            found_ids = set()
            for row in rows:
                username = row.get("username", "")
                if not username:
                    continue
                found_ids.add(username)
                biz_type = int(row.get("type", 0) or 0)
                is_service = biz_type == 1  # biz_info.type=1 → 服务号
                _service_ids_cache[username] = is_service
                if is_service:
                    service_ids.add(username)
            # Cache rows not found in biz_info as needing fallback check
            still_missing = [gid for gid in missing_ids if gid not in found_ids]
            if not still_missing:
                if service_ids:
                    names = []
                    for s in gh_sessions:
                        if s.get("username", "") in service_ids:
                            names.append(s.get("displayName", s.get("username", "")))
                    logger.info("Filtered service accounts (biz_info.type=1): %s", names)
                return service_ids
            # Some ids not found in biz_info, fall through to verify_flag for those
            missing_ids = still_missing
    except Exception as e:
        logger.warning("biz_info query failed, falling back to verify_flag: %s", e)

    # Fallback: verify_flag & 16 for ids not resolved by biz_info
    try:
        quoted = ",".join(f"'{gid}'" for gid in missing_ids)
        sql = f"SELECT username, verify_flag FROM contact WHERE username IN ({quoted})"
        rows = client.exec_query("contact", "", sql)
        found_ids = set()
        for row in rows:
            username = row.get("username", "")
            if not username:
                continue
            found_ids.add(username)
            vflag = int(row.get("verify_flag", 0) or 0)
            is_service = bool(vflag & 16)  # bit 4 = service account (不够精确)
            _service_ids_cache[username] = is_service
            if is_service:
                service_ids.add(username)

        # Cache missing contact rows as non-service only after a successful query.
        for username in missing_ids:
            if username not in found_ids:
                _service_ids_cache[username] = False

        if service_ids:
            names = []
            for s in gh_sessions:
                if s.get("username", "") in service_ids:
                    names.append(s.get("displayName", s.get("username", "")))
            logger.info("Filtered service accounts (verify_flag & 16 fallback): %s", names)

    except Exception as e:
        logger.warning("Failed to query contact.verify_flag for service account detection: %s", e)

    return service_ids
