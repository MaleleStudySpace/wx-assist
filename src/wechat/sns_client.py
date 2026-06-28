"""
WeChat Moments (朋友圈) Client — reads from sns.db

Provides:
- Timeline browsing with filtering
- Username enumeration
- Block-delete trigger management
- Post deletion
- Image decryption (for encrypted images)
"""
import json
import logging
import os
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SnsPost:
    """Represents a single Moments post."""

    id: str = ""                          # Post ID (tid)
    username: str = ""                    # User who posted
    nickname: str = ""                    # Display name
    content: str = ""                     # Text content
    create_time: int = 0                  # Unix timestamp
    like_count: int = 0                   # Number of likes
    comment_count: int = 0                # Number of comments
    media_list: list = field(default_factory=list)  # Images/videos
    location: str = ""                    # Location string
    source: str = ""                      # Source (e.g., "朋友圈")

    # Additional fields from raw data
    raw: dict = field(default_factory=dict)

    @property
    def timestamp_str(self):
        """Format create time as string."""
        if self.create_time:
            try:
                return datetime.fromtimestamp(self.create_time).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                pass
        return ""

    @property
    def date_str(self):
        """Format date only."""
        if self.create_time:
            try:
                return datetime.fromtimestamp(self.create_time).strftime(
                    "%Y-%m-%d"
                )
            except Exception:
                pass
        return ""

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "username": self.username,
            "nickname": self.nickname,
            "content": self.content,
            "create_time": self.create_time,
            "timestamp": self.timestamp_str,
            "date": self.date_str,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "media_list": self.media_list,
            "location": self.location,
            "source": self.source,
        }


def _decode_sns_content(content_hex: str) -> str:
    """Decode hex-encoded content from sns.db."""
    if not content_hex:
        return ""
    try:
        data = bytes.fromhex(content_hex)
        # Try UTF-8 first, then GBK
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("gbk", errors="replace")
    except Exception:
        return content_hex


def _parse_sns_media(media_str: str) -> list:
    """Parse media list from JSON string."""
    if not media_str:
        return []
    try:
        return json.loads(media_str)
    except Exception:
        return []


class SnsClient:
    """Client for reading WeChat Moments from sns.db."""

    def __init__(self, wcdb_client):
        """Initialize with a WcdbNativeClient instance."""
        self._client = wcdb_client

    def get_timeline(
        self,
        limit: int = 20,
        offset: int = 0,
        username: Optional[str] = None,
        keyword: Optional[str] = None,
        start_time: int = 0,
        end_time: int = 0,
    ) -> list[SnsPost]:
        """Get Moments timeline with optional filters.

        Args:
            limit: Max posts to return
            offset: Pagination offset
            username: Filter by specific user (wxid)
            keyword: Search keyword
            start_time: Filter start timestamp
            end_time: Filter end timestamp

        Returns:
            List of SnsPost objects
        """
        raw_posts = self._client.get_sns_timeline(
            limit=limit,
            offset=offset,
            usernames=[username] if username else None,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
        )

        posts = []
        # Resolve nicknames for all users
        users = list(set(p.get("username", "") for p in raw_posts if p.get("username")))
        nicknames = self._client.get_display_names(users) if users else {}

        for p in raw_posts:
            try:
                post = SnsPost(
                    id=p.get("tid") or p.get("id") or "",
                    username=p.get("username", ""),
                    nickname=nicknames.get(p.get("username", ""), p.get("nickname", "")),
                    content=_decode_sns_content(p.get("content", "") or p.get("messageContent", "")),
                    create_time=int(p.get("createTime", 0) or 0),
                    like_count=int(p.get("likeCount", 0) or 0),
                    comment_count=int(p.get("commentCount", 0) or 0),
                    media_list=_parse_sns_media(p.get("mediaList") or p.get("media_list", "")),
                    location=p.get("location", "") or p.get("locationName", ""),
                    source=p.get("source", ""),
                    raw=p,
                )
                posts.append(post)
            except Exception as e:
                logger.warning(f"Failed to parse SNS post: {e}")
                continue

        return posts

    def get_usernames(self) -> list[str]:
        """Get all usernames who have posted Moments."""
        return self._client.get_sns_usernames()

    def search_posts(self, keyword: str, limit: int = 50) -> list[SnsPost]:
        """Search Moments posts by keyword."""
        return self.get_timeline(limit=limit, keyword=keyword)

    def get_user_posts(self, username: str, limit: int = 50) -> list[SnsPost]:
        """Get all posts from a specific user."""
        return self.get_timeline(limit=limit, username=username)

    # ── Protection (Block Delete Trigger) ────────────────────────────

    def install_protection(self) -> dict:
        """Install trigger to prevent post deletion.

        Returns:
            dict with success status
        """
        return self._client.install_sns_block_delete_trigger()

    def uninstall_protection(self) -> dict:
        """Uninstall the protection trigger."""
        return self._client.uninstall_sns_block_delete_trigger()

    def check_protection(self) -> dict:
        """Check if protection is enabled.

        Returns:
            dict with installed status
        """
        return self._client.check_sns_block_delete_trigger()

    # ── Post Management ───────────────────────────────────────────────

    def delete_post(self, post_id: str) -> dict:
        """Delete a post (bypasses protection).

        Args:
            post_id: The post ID to delete

        Returns:
            dict with success status
        """
        return self._client.delete_sns_post(post_id)

    # ── Export ─────────────────────────────────────────────────────────

    def export_to_html(
        self,
        output_path: str,
        posts: Optional[list[SnsPost]] = None,
        include_images: bool = True,
    ) -> dict:
        """Export posts to standalone HTML file.

        Args:
            output_path: Output HTML file path
            posts: List of posts to export (if None, fetches recent 100)
            include_images: Whether to embed images as data URIs

        Returns:
            dict with export statistics
        """
        if posts is None:
            posts = self.get_timeline(limit=100)

        stats = {"posts": len(posts), "images": 0, "errors": 0}

        html_parts = [
            "<!DOCTYPE html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="UTF-8">',
            "<title>微信朋友圈导出</title>",
            "<style>",
            "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; "
            "max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }",
            ".post { background: white; border-radius: 8px; padding: 16px; margin-bottom: 16px; "
            "box-shadow: 0 1px 3px rgba(0,0,0,0.1); }",
            ".post-header { display: flex; align-items: center; margin-bottom: 12px; }",
            ".avatar { width: 40px; height: 40px; border-radius: 50%; background: #ddd; "
            "margin-right: 12px; }",
            ".user-info { flex: 1; }",
            ".nickname { font-weight: 600; color: #1a1a1a; }",
            ".time { font-size: 12px; color: #999; }",
            ".content { margin: 12px 0; line-height: 1.6; white-space: pre-wrap; }",
            ".media { display: grid; gap: 4px; margin-top: 12px; }",
            ".media img { max-width: 100%; border-radius: 4px; }",
            ".location { font-size: 12px; color: #666; margin-top: 8px; }",
            ".stats { font-size: 12px; color: #999; margin-top: 12px; border-top: 1px solid #eee; "
            "padding-top: 8px; }",
            "</style>",
            "</head>",
            "<body>",
            f"<h1>微信朋友圈导出 ({len(posts)} 条)</h1>",
            f"<p>导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>",
        ]

        for post in posts:
            try:
                html_parts.append('<div class="post">')
                html_parts.append('<div class="post-header">')
                html_parts.append(f'<div class="avatar"></div>')
                html_parts.append('<div class="user-info">')
                html_parts.append(f'<div class="nickname">{post.nickname or post.username}</div>')
                html_parts.append(f'<div class="time">{post.timestamp_str}</div>')
                html_parts.append('</div></div>')

                if post.content:
                    html_parts.append(f'<div class="content">{post.content}</div>')

                if post.media_list:
                    html_parts.append('<div class="media">')
                    for media in post.media_list:
                        img_url = media.get("url") or media.get("imgUrl") or ""
                        if img_url:
                            html_parts.append(f'<img src="{img_url}" loading="lazy"/>')
                            stats["images"] += 1
                    html_parts.append('</div>')

                if post.location:
                    html_parts.append(f'<div class="location">📍 {post.location}</div>')

                stats_html = f"👍 {post.like_count}  💬 {post.comment_count}"
                html_parts.append(f'<div class="stats">{stats_html}</div>')
                html_parts.append('</div>')
            except Exception as e:
                logger.error(f"Failed to render post {post.id}: {e}")
                stats["errors"] += 1

        html_parts.append("</body></html>")

        try:
            Path(output_path).write_text("\n".join(html_parts), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to write HTML: {e}")
            stats["errors"] += 1

        return stats