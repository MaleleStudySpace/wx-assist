"""向量存储 + 检索。ChromaDB 实现。

负责：
1. 存储向量和元数据到磁盘
2. HNSW 近似最近邻搜索
3. 按 source/chat_id/时间范围等元数据前过滤
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

from .models import Chunk, SearchResult

logger = logging.getLogger(__name__)


class VectorStore(ABC):
    """向量存储接口。"""

    @abstractmethod
    def add(self, chunks: list[Chunk], embeddings: np.ndarray):
        """批量写入。相同 ID 重复写入覆盖。

        Args:
            chunks: Chunk 列表（与 embeddings 一一对应）
            embeddings: float32 矩阵，(len(chunks), dim)
        """
        ...

    @abstractmethod
    def search(self, query_emb: np.ndarray, top_k: int,
               where: Optional[dict] = None) -> list[SearchResult]:
        """检索。失败返回空列表。

        Args:
            query_emb: 查询向量，shape=(dim,)
            top_k: 返回条数
            where: 过滤条件，如 {"source": "msg", "chat_id": "xxx"}
        Returns:
            SearchResult 列表，按相似度降序排序
        """
        ...

    @abstractmethod
    def delete_by_source(self, source: str, source_ids: list[str]):
        """按数据源 + ID 删除。用于撤回同步。"""
        ...

    @abstractmethod
    def close(self):
        """关闭连接，释放资源。"""
        ...

    @abstractmethod
    def delete_older_than(self, cutoff_ts: int) -> int:
        """删除早于 cutoff_ts 的 chunk。返回删除条数。"""
        ...


class ChromaStore(VectorStore):
    """ChromaDB 实现。数据在磁盘，不占内存。"""

    def __init__(self, path: str = "data/chroma"):
        self._path = path
        self._client = None
        self._collection = None

    def warmup(self):
        """初始化 ChromaDB 客户端和 collection。"""
        try:
            # 禁用 ChromaDB 遥测，避免因 posthog 等 telemetry 依赖缺失而报错
            import os as _os
            _os.environ["CHROMA_TELEMETRY_DISABLED"] = "TRUE"

            import chromadb

            self._client = chromadb.PersistentClient(path=self._path)
            self._collection = self._client.get_or_create_collection(
                name="rag",
                metadata={"hnsw:space": "cosine"},
            )
            logger.info("[RAG] ChromaDB 就绪: path=%s", self._path)
        except Exception as e:
            logger.warning("[RAG] ChromaDB 初始化失败: %s", e)
            raise

    def add(self, chunks: list[Chunk], embeddings: np.ndarray):
        if self._collection is None:
            raise RuntimeError("ChromaStore 未初始化")

        try:
            self._collection.add(
                ids=[c.id for c in chunks],
                embeddings=embeddings.tolist(),
                metadatas=[{
                    "source": c.source,
                    "source_id": c.source_id,
                    "chat_id": c.chat_id,
                    "sender_name": c.sender_name,
                    "created_at": c.created_at,
                    "created_at_ts": int(c.created_at) if c.created_at and c.created_at.isdigit() else 0,
                    "content": c.content,
                } for c in chunks],
                # 不传 documents — 我们只做向量搜索，不需要 FTS5 索引
            )
        except Exception as e:
            logger.warning("[RAG] ChromaDB add 失败: %s", e)

    def search(self, query_emb: np.ndarray, top_k: int,
               where: Optional[dict] = None) -> list[SearchResult]:
        if self._collection is None:
            return []

        try:
            q = query_emb.reshape(1, -1).tolist()
            # ChromaDB 的 where 要求多条件使用 $and/$or 语法
            chroma_where = self._normalize_where(where)
            results = self._collection.query(
                query_embeddings=q,
                n_results=top_k,
                where=chroma_where,
            )
        except Exception as e:
            logger.warning("[RAG] ChromaDB search 失败: %s", e)
            return []

        # 解析返回结果
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        documents = results.get("documents", [[]])[0]

        search_results = []
        for i in range(len(ids)):
            meta = metadatas[i] if i < len(metadatas) else {}
            # content 优先从 metadata 取（新格式），兼容旧格式（documents）
            content = meta.get("content")
            if not content and i < len(documents):
                content = documents[i]
            chunk = Chunk(
                id=ids[i],
                source=meta.get("source", ""),
                source_id=meta.get("source_id", ""),
                chat_id=meta.get("chat_id", ""),
                sender_name=meta.get("sender_name", ""),
                content=content or "",
                created_at=meta.get("created_at", ""),
            )
            score = float(distances[i]) if i < len(distances) else 0.0
            # ChromaDB 返回的是距离，余弦距离 ≈ 1 - 余弦相似度
            search_results.append(SearchResult(chunk=chunk, score=1.0 - score))

        return search_results

    def delete_by_source(self, source: str, source_ids: list[str]):
        if self._collection is None:
            return

        try:
            where = {"$and": [
                {"source": source},
                {"source_id": {"$in": source_ids}},
            ]}
            self._collection.delete(where=where)
        except Exception as e:
            logger.warning("[RAG] ChromaDB delete 失败: %s", e)

    def delete_older_than(self, cutoff_ts: int) -> int:
        """删除 created_at < cutoff_ts 的所有 chunk。返回删除条数。

        Args:
            cutoff_ts: UNIX 时间戳（秒），早于此时间的 chunk 被删除
        """
        if self._collection is None:
            return 0
        try:
            # 先查符合条件的数量
            results = self._collection.get(
                where={"created_at_ts": {"$lt": cutoff_ts}},
                limit=999999,
            )
            ids = results.get("ids", [])
            if not ids:
                return 0
            self._collection.delete(ids=ids)
            logger.info("[RAG] ChromaDB delete_older_than: 删除了 %d 个 chunk (cutoff=%d)",
                        len(ids), cutoff_ts)
            return len(ids)
        except Exception as e:
            logger.warning("[RAG] ChromaDB delete_older_than 失败: %s", e)
            return 0

    def _normalize_where(self, where: Optional[dict]) -> Optional[dict]:
        """将扁平 dict 转为 ChromaDB 的 where 语法。

        ChromaDB 要求多条件使用 {"$and": [...]}。
        """
        if not where:
            return None
        if len(where) == 1:
            return where
        items = [{k: v} for k, v in where.items()]
        return {"$and": items}

    def close(self):
        self._client = None
        self._collection = None
        logger.info("[RAG] ChromaDB 已关闭")
