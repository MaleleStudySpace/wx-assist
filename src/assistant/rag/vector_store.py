"""向量存储 + 检索。ChromaDB 实现。

负责：
1. 存储向量和元数据到磁盘
2. HNSW 近似最近邻搜索
3. 按 source/chat_id/时间范围等元数据前过滤
"""

import logging
import multiprocessing
from pathlib import Path
import threading
from abc import ABC, abstractmethod
from typing import Optional

import numpy as np

from .models import Chunk, SearchResult
from .chroma_worker import run_chroma_worker

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
    """Persistent vectors owned by a worker process with bounded RPC waits.

    Native Chroma/HNSW recovery can hold the GIL indefinitely. A thread timeout
    cannot protect the desktop process from that failure; a process can.
    """

    def __init__(self, path: str = "data/chroma", timeout: float = 30.0):
        self._path = str(Path(path).resolve())
        self._timeout = timeout
        self._process = None
        self._connection = None
        self._failure = None
        self._write_lock = threading.RLock()

    def _discard_worker(self):
        process, connection = self._process, self._connection
        self._process = self._connection = None
        if connection is not None:
            connection.close()
        if process is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            if not process.is_alive():
                process.close()

    def _fail(self, reason):
        self._failure = (
            f"RAG 向量索引不可用 ({self._path}): {reason}。"
            "请停止应用后重建 RAG 索引；聊天数据库无需删除。"
        )
        self._discard_worker()
        raise RuntimeError(self._failure)

    def _exchange(self, timeout, request=None):
        # Bound the entire transfer, including send() and recv(): a worker can
        # stall before reading a large payload or while sending its response.
        connection = self._connection
        done = threading.Event()
        response = []

        def transfer():
            try:
                if request is not None:
                    connection.send(request)
                response.append(connection.recv())
            except (EOFError, OSError) as exc:
                response.append((False, f"ChromaDB 子进程已退出: {exc}"))
            finally:
                done.set()

        threading.Thread(target=transfer, name="rag-chroma-rpc", daemon=True).start()
        if not done.wait(timeout):
            self._fail(f"ChromaDB 操作超过 {timeout:g} 秒")
        ok, result = response[0]
        if not ok:
            self._fail(result)
        return result

    def warmup(self):
        with self._write_lock:
            if self._failure:
                raise RuntimeError(self._failure)
            if self._process is not None:
                return
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe()
            process = context.Process(
                target=run_chroma_worker, args=(child, self._path),
                name="rag-chroma", daemon=True,
            )
            try:
                process.start()
            except Exception:
                parent.close()
                child.close()
                process.close()
                raise
            child.close()
            self._process, self._connection = process, parent
            count = self._exchange(self._timeout)
            logger.info("[RAG] ChromaDB 就绪: path=%s count=%d", self._path, count)

    def _request(self, command, payload=None, timeout=None):
        with self._write_lock:
            if self._failure:
                raise RuntimeError(self._failure)
            if self._connection is None:
                raise RuntimeError("ChromaStore 未初始化")
            return self._exchange(
                self._timeout if timeout is None else timeout, (command, payload),
            )

    def add(self, chunks: list[Chunk], embeddings: np.ndarray):
        # Bound IPC payloads and Chroma batches, and make retries idempotent.
        for offset in range(0, len(chunks), 128):
            batch = chunks[offset:offset + 128]
            self._request("upsert", {
                "ids": [c.id for c in batch],
                "embeddings": embeddings[offset:offset + 128].tolist(),
                "metadatas": [{
                    "source": c.source,
                    "source_id": c.source_id,
                    "chat_id": c.chat_id,
                    "sender_name": c.sender_name,
                    "created_at": c.created_at,
                    "created_at_ts": int(c.created_at) if c.created_at and c.created_at.isdigit() else 0,
                    "content": c.content,
                } for c in batch],
            })

    def search(self, query_emb: np.ndarray, top_k: int,
               where: Optional[dict] = None) -> list[SearchResult]:
        try:
            results = self._request("query", {
                "query_embeddings": query_emb.reshape(1, -1).tolist(),
                "n_results": top_k,
                "where": self._normalize_where(where),
                "include": ["metadatas", "documents", "distances"],
            })
        except Exception as exc:
            logger.warning("[RAG] ChromaDB search 失败: %s", exc)
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
        if not source_ids:
            return
        self._request("delete", {"where": {"$and": [
            {"source": source}, {"source_id": {"$in": source_ids}},
        ]}})

    def delete_older_than(self, cutoff_ts: int) -> int:
        return self._request("delete_older_than", cutoff_ts)

    def count(self) -> int:
        return self._request("count")

    def _normalize_where(self, where: Optional[dict]) -> Optional[dict]:
        if not where:
            return None
        if len(where) == 1:
            return where
        return {"$and": [{k: v} for k, v in where.items()]}

    def close(self):
        with self._write_lock:
            if self._connection is not None:
                try:
                    self._request("close", timeout=5)
                    self._process.join(timeout=2)
                except Exception as exc:
                    logger.warning("[RAG] ChromaDB close: %s", exc)
            self._discard_worker()
