"""
WeChat Favorites (收藏) Reader — reads from fav.db

Implements XML parsing for different favorite item types:
- text: Plain text
- image: Images with thumbnails
- video: Videos
- link: Web links
- file: Attachments
- location: Location info
- contact: Business cards (名片)
- article: WeChat articles (微信公众号文章)
"""
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Favorite item type constants
FAV_TYPE_TEXT = 1
FAV_TYPE_IMAGE = 2
FAV_TYPE_VIDEO = 4
FAV_TYPE_LINK = 5
FAV_TYPE_FILE = 8
FAV_TYPE_LOCATION = 16
FAV_TYPE_CONTACT = 17
FAV_TYPE_ARTICLE = 33  # 微信文章


class FavItem:
    """Represents a single favorite item."""

    def __init__(self, row: dict):
        self.local_id = row.get("localId", 0) or row.get("id", 0)
        self.create_time = int(row.get("createTime", 0) or 0)
        self.update_time = int(row.get("updateTime", 0) or 0)
        self.fav_type = int(row.get("favType", 0) or row.get("type", 0))
        self.content = row.get("content", "") or row.get("xmlContent", "") or ""
        self.title = row.get("title", "") or ""
        self.source = row.get("source", "") or ""
        # Additional fields from FavItem table
        self.link = row.get("link", "") or row.get("url", "") or ""
        self.thumb_url = row.get("thumbUrl", "") or row.get("thumb_url", "") or ""
        self.media_path = row.get("mediaPath", "") or row.get("file_path", "") or ""
        self.latitude = row.get("latitude", 0.0) or 0.0
        self.longitude = row.get("longitude", 0.0) or 0.0
        self.poi_name = row.get("poiName", "") or row.get("location_name", "") or ""
        self.username = row.get("username", "") or row.get("userName", "") or ""  # for contact

    @property
    def type_name(self):
        """Human-readable type name."""
        names = {
            FAV_TYPE_TEXT: "text",
            FAV_TYPE_IMAGE: "image",
            FAV_TYPE_VIDEO: "video",
            FAV_TYPE_LINK: "link",
            FAV_TYPE_FILE: "file",
            FAV_TYPE_LOCATION: "location",
            FAV_TYPE_CONTACT: "contact",
            FAV_TYPE_ARTICLE: "article",
        }
        return names.get(self.fav_type, f"unknown({self.fav_type})")

    @property
    def timestamp_str(self):
        """Format create time as string."""
        if self.create_time:
            try:
                return datetime.fromtimestamp(self.create_time).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        return ""

    def parse_content(self) -> dict:
        """Parse the XML content and extract structured data."""
        if not self.content:
            return {}

        result = {
            "type": self.type_name,
            "raw_content": self.content,
            "items": [],
        }

        try:
            # Try to parse as XML
            root = ET.fromstring(self.content)
            result["items"] = self._parse_xml_items(root)
        except ET.ParseError:
            # Not valid XML, might be plain text
            result["text_content"] = self.content.strip()

        # Extract article-specific data if present
        if self.fav_type == FAV_TYPE_ARTICLE or "<mmread>" in self.content:
            article_data = self._parse_article()
            result.update(article_data)

        return result

    def _parse_xml_items(self, root) -> list:
        """Parse XML and extract item elements."""
        items = []
        # Common patterns for items
        for elem in root.iter():
            if elem.tag in ("item", "Item", "msg", "Msg"):
                item_data = {}
                if elem.text:
                    item_data["text"] = elem.text.strip()
                for child in elem:
                    item_data[child.tag.lower()] = child.text or ""
                if item_data:
                    items.append(item_data)
        return items

    def _parse_article(self) -> dict:
        """Extract WeChat article (公众号文章) data."""
        data = {
            "is_article": False,
            "article_title": "",
            "article_url": "",
            "article_source": "",
            "article_digest": "",
        }

        if not self.content:
            return data

        # Check for mmread or appmsg tags
        if "<mmread>" not in self.content and "<appmsg>" not in self.content:
            return data

        data["is_article"] = True

        # Extract title
        title_match = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", self.content, re.DOTALL)
        if title_match:
            data["article_title"] = title_match.group(1).strip()

        # Extract URL
        url_match = re.search(r"<url>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</url>", self.content, re.DOTALL)
        if url_match:
            data["article_url"] = url_match.group(1).strip()

        # Extract source name (from category/name or sourcename)
        source_match = re.search(r"<category[^>]*>.*?<name>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</name>.*?</category>",
                                  self.content, re.DOTALL)
        if source_match:
            data["article_source"] = source_match.group(1).strip()
        else:
            source_match = re.search(r"<sourcename>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</sourcename>",
                                      self.content, re.DOTALL)
            if source_match:
                data["article_source"] = source_match.group(1).strip()

        # Extract digest/description
        digest_match = re.search(r"<des>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</des>", self.content, re.DOTALL)
        if digest_match:
            data["article_digest"] = digest_match.group(1).strip()

        return data

    def to_dict(self) -> dict:
        """Convert to dictionary for export."""
        parsed = self.parse_content()
        return {
            "id": self.local_id,
            "type": self.type_name,
            "fav_type": self.fav_type,
            "title": self.title,
            "source": self.source,
            "create_time": self.create_time,
            "timestamp": self.timestamp_str,
            "link": self.link,
            "parsed": parsed,
        }

    def to_markdown(self) -> str:
        """Convert to Markdown format."""
        lines = [f"# {self.title or f'收藏 {self.local_id}'}"]
        lines.append(f"\n**类型**: {self.type_name}")
        lines.append(f"**创建时间**: {self.timestamp_str}")
        if self.source:
            lines.append(f"**来源**: {self.source}")

        parsed = self.parse_content()

        if parsed.get("is_article"):
            lines.append("\n## 📄 文章信息")
            if parsed.get("article_url"):
                lines.append(f"- [阅读原文]({parsed['article_url']})")
            if parsed.get("article_source"):
                lines.append(f"- 来源: {parsed['article_source']}")
            if parsed.get("article_digest"):
                lines.append(f"\n**摘要**: {parsed['article_digest']}")

        # Add text content
        text = parsed.get("text_content", "") or parsed.get("raw_content", "")
        if text:
            lines.append("\n## 📝 内容\n")
            lines.append(text[:2000])  # Truncate long content

        return "\n".join(lines)


class FavReader:
    """Reads favorites from WeChat's fav.db database."""

    def __init__(self, wcdb_client):
        """Initialize with a WcdbNativeClient instance."""
        self._client = wcdb_client

    def get_all(self, limit=200, offset=0) -> list[FavItem]:
        """Get all favorite items."""
        rows = self._client.get_favorites(limit=limit, offset=offset)
        return [FavItem(row) for row in rows]

    def get_by_type(self, fav_type: int, limit=200) -> list[FavItem]:
        """Get favorites by type."""
        # This would need a SQL filter in exec_query
        all_items = self.get_all(limit=limit * 2)  # Fetch more to filter
        return [item for item in all_items if item.fav_type == fav_type]

    def get_articles(self) -> list[FavItem]:
        """Get only article-type favorites."""
        return self.get_by_type(FAV_TYPE_ARTICLE)

    def get_texts(self) -> list[FavItem]:
        """Get only text-type favorites."""
        return self.get_by_type(FAV_TYPE_TEXT)

    def get_links(self) -> list[FavItem]:
        """Get only link-type favorites."""
        return self.get_by_type(FAV_TYPE_LINK)

    def export_markdown(self, output_dir: str) -> dict:
        """Export all favorites to Markdown files.

        Args:
            output_dir: Base directory for export

        Returns:
            dict with export statistics
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        favorites = self.get_all(limit=5000)
        stats = {"total": len(favorites), "exported": 0, "errors": 0}

        index_entries = []

        for fav in favorites:
            try:
                # Create time-based directory
                dt = datetime.fromtimestamp(fav.create_time) if fav.create_time else datetime.now()
                year_dir = output_path / str(dt.year)
                month_dir = year_dir / f"{dt.month:02d}"
                day_dir = month_dir / f"{dt.day:02d}"
                day_dir.mkdir(parents=True, exist_ok=True)

                # Generate filename
                time_str = dt.strftime("%Y%m%d_%H%M%S")
                safe_title = re.sub(r'[<>:"/\\|?*]', '_', (fav.title or f"fav_{fav.local_id}")[:50])
                filename = f"{time_str}_{fav.type_name}_{safe_title}.md"
                file_path = day_dir / filename

                # Write markdown
                content = fav.to_markdown()
                file_path.write_text(content, encoding="utf-8")

                # Add to index
                index_entries.append({
                    "id": fav.local_id,
                    "type": fav.type_name,
                    "title": fav.title,
                    "timestamp": fav.timestamp_str,
                    "file_path": str(file_path.relative_to(output_path)),
                })

                stats["exported"] += 1
            except Exception as e:
                logger.error(f"Failed to export fav {fav.local_id}: {e}")
                stats["errors"] += 1

        # Write index
        index_path = output_path / "index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_entries, f, ensure_ascii=False, indent=2)

        return stats

    def export_json(self, output_path: str) -> dict:
        """Export all favorites to JSON Lines format.

        Args:
            output_path: Output .jsonl file path

        Returns:
            dict with export statistics
        """
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        favorites = self.get_all(limit=5000)
        stats = {"total": len(favorites), "exported": 0, "errors": 0}

        with open(output_file, "w", encoding="utf-8") as f:
            for fav in favorites:
                try:
                    json.dump(fav.to_dict(), f, ensure_ascii=False)
                    f.write("\n")
                    stats["exported"] += 1
                except Exception as e:
                    logger.error(f"Failed to export fav {fav.local_id} to JSON: {e}")
                    stats["errors"] += 1

        return stats