"""Message content codec — 微信消息 content 的编解码公共函数。

微信 4.x 的 WCDB 将大部分消息 content 存储为 hex 编码的 zstd 压缩数据
（魔数 0x28B52FFD，即 hex 开头的 "28b52ffd"）。必须解压后才能得到
可读文本（纯文本 / XML / "sender_id:\\n内容" 群聊格式）。

本模块为所有消费消息 content 的链路提供统一入口：
- WEBUI 会话管理（api_handlers）在读取 WCDB 时解压
- 入库链路（wcdb_backend._standardize）在写入 messages.db 前解压，
  保证本地缓存表里存的是清晰可读文本
"""

import logging

logger = logging.getLogger(__name__)


def decompress_content(content: str) -> str:
    """Decompress zstd-compressed hex-encoded message content.

    WCDB stores most message content as hex-encoded zstd-compressed data.
    The zstd magic number is 0x28B52FFD, which appears as "28b52ffd" in hex.
    After decompression, the content is either plain text, XML, or
    sender_id:\\ncontent (group chat format).

    解压失败（非 zstd / 非 hex / 解压出乱码）时原样返回，不抛异常。
    """
    if not content or len(content) < 16:
        return content

    # Quick check: is this hex-encoded data?
    try:
        is_hex = all(c in '0123456789abcdef' for c in content[:100].lower())
    except Exception:
        return content

    if not is_hex:
        return content

    # Check for zstd magic (28b52ffd) at the start
    if not content.lower().startswith('28b52ffd'):
        return content

    try:
        raw = bytes.fromhex(content)
        import zstandard
        dctx = zstandard.ZstdDecompressor()
        decompressed = dctx.decompress(raw, max_output_size=10 * 1024 * 1024)
        text = decompressed.decode('utf-8', errors='replace')
        # If >20% replacement chars, decompression likely produced garbage
        replacement_count = text.count('�')
        if len(text) > 0 and replacement_count > len(text) * 0.2:
            return content
        return text
    except Exception:
        return content


def strip_wxid_prefix(content: str) -> str:
    """Strip sender ID prefix from group chat message content.

    In WeChat group chats, messages are stored as 'sender_id:\\nactual text'
    or 'sender_id:actual text'. The sender ID can be:
    - wxid_xxx (WeChat ID)
    - qq123456789 (QQ number)
    - Other alphanumeric IDs

    This prefix should not be displayed to the user.
    """
    import re as _re
    # Match sender_id followed by colon at the start, optionally followed by newline
    # Sender IDs can be: wxid_xxx, qq123, 12345@openim, user@domain, etc.
    return _re.sub(r'^[a-zA-Z0-9_@.\-]+:\n?', '', content)
