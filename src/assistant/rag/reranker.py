"""重排序接口。Phase 3 实现，目前仅占位。

Reranker 将粗召结果（Top-50）精排为 Top-5。
可在不改变其他代码的前提下启用。
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from .models import SearchResult

logger = logging.getLogger(__name__)


class Reranker(ABC):
    """重排序接口。接收 query 和粗召结果，返回精排结果。"""

    @abstractmethod
    def rerank(self, query: str, results: list[SearchResult],
               top_n: int = 5) -> list[SearchResult]:
        ...

    @abstractmethod
    def warmup(self):
        ...


class NoopReranker(Reranker):
    """空实现。不重排序，直接返回原始结果的前 top_n 条。

    Phase 2 使用此实现，Phase 3 替换为真正的 Reranker。
    """

    def rerank(self, query: str, results: list[SearchResult],
               top_n: int = 5) -> list[SearchResult]:
        return results[:top_n]

    def warmup(self):
        pass
