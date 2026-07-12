"""RAG 数据类定义（与 rag_types 共享，保持向后兼容）。

所有数据类已移至 rag_types，此处为 re-export。
"""
from ..rag_types import Chunk, SearchResult, ChunkConfig

__all__ = ["Chunk", "SearchResult", "ChunkConfig"]
