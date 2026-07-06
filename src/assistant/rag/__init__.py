"""RAG 子系统 — 基于 fastembed + ChromaDB 的轻量语义搜索。"""
from .engine import RAGEngine
from .chunking import ChunkingStrategy, SlidingWindowChunker, SingleItemChunker
from .embedder import Embedder, FastEmbedder
from .vector_store import VectorStore, ChromaStore
from .reranker import Reranker, NoopReranker
from .models import Chunk, SearchResult, ChunkConfig

__all__ = [
    "RAGEngine",
    "ChunkingStrategy", "SlidingWindowChunker", "SingleItemChunker",
    "Embedder", "FastEmbedder",
    "VectorStore", "ChromaStore",
    "Reranker", "NoopReranker",
    "Chunk", "SearchResult", "ChunkConfig",
]
