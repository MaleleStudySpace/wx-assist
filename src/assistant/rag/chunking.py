"""分块策略。

解决单条消息太短导致 Embedding 信息量不足的问题。
将连续多条消息拼接成一段完整对话后再编码。
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from .models import Chunk, ChunkConfig

logger = logging.getLogger(__name__)


class ChunkingStrategy(ABC):
    """分块策略接口。不同数据源使用不同实现。"""

    @abstractmethod
    def chunk(self, items: list[dict], source: str) -> list[Chunk]:
        """批量分块。用于冷启动/定时批量索引。

        Args:
            items: 原始消息列表，每项含 id/chat_id/sender_name/content/created_at
            source: 数据源标识 "msg" | "fav" | "sns"
        Returns:
            Chunk 列表，空列表表示无有效 chunk
        """
        ...

    @abstractmethod
    def chunk_one(self, item: dict, source: str,
                  prev_items: list[dict]) -> Optional[Chunk]:
        """增量单条分块。配合 RAGEngine 的 buffer 使用。

        传入 buffer 中已累积的前几条，判断是否凑够 window 条。
        不够则返回 None（等待下一条）。

        Args:
            item: 当前消息
            source: 数据源标识
            prev_items: buffer 中已累积的前几条（不含 item）
        Returns:
            凑够 window 条时返回 Chunk，否则 None
        """
        ...


class SlidingWindowChunker(ChunkingStrategy):
    """滑动窗口分块。聊天记录默认策略。

    每次取 window 条消息拼接，步长为 stride（= window - 重叠数）。
    保证冷启动和增量产生的 chunk 粒度一致。
    """

    def __init__(self, config: Optional[ChunkConfig] = None):
        self.config = config or ChunkConfig()

    def chunk(self, items: list[dict], source: str) -> list[Chunk]:
        if not items:
            return []

        chunks = []
        for i in range(0, len(items), self.config.stride):
            group = items[i:i + self.config.window]
            if len(group) < self.config.min_length:
                continue

            chunks.append(self._make_chunk(group, source))

        return chunks

    def chunk_one(self, item: dict, source: str,
                  prev_items: list[dict]) -> Optional[Chunk]:
        groups = prev_items + [item]
        if len(groups) < self.config.window:
            return None

        # 取最后 window 条
        group = groups[-self.config.window:]
        return self._make_chunk(group, source)

    def _make_chunk(self, group: list[dict], source: str) -> Chunk:
        lines = [
            f"{m.get('sender_name', '')}: {m.get('content', '')}"
            for m in group
        ]
        content = "\n".join(lines)
        last = group[-1]

        return Chunk(
            id=f"{source}_{last.get('id', '0')}_{id(group)}",
            source=source,
            source_id=str(last.get('id', '0')),
            chat_id=str(last.get('chat_id', '')),
            sender_name=last.get('sender_name', ''),
            content=content,
            created_at=str(last.get('created_at', '')),
        )


class SingleItemChunker(ChunkingStrategy):
    """单条分块。适合收藏/朋友圈等已完成态内容。"""

    def __init__(self, config: Optional[ChunkConfig] = None):
        self.config = config or ChunkConfig(window=1, stride=1, min_length=1)

    def chunk(self, items: list[dict], source: str) -> list[Chunk]:
        return [self._make_chunk(item, source) for item in items if item.get('content')]

    def chunk_one(self, item: dict, source: str,
                  prev_items: list[dict]) -> Optional[Chunk]:
        return self._make_chunk(item, source)

    def _make_chunk(self, item: dict, source: str) -> Chunk:
        return Chunk(
            id=f"{source}_{item.get('id', '0')}",
            source=source,
            source_id=str(item.get('id', '0')),
            chat_id=str(item.get('chat_id', '')),
            sender_name=item.get('sender_name', ''),
            content=item.get('content', ''),
            created_at=str(item.get('created_at', '')),
        )
