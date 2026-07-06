"""RAGEngine — 编排层。

对外暴露两个核心方法：
  - ingest() / ingest_one(): 消息 → 分块 → 编码 → 存储
  - search(): 用户提问 → 编码 → 检索 → 重排序 → 拼上下文

对现有系统零影响：所有方法 try/except 包裹，失败静默降级。
"""

import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

from . import chunking as chunking_mod
from .embedder import Embedder
from .models import Chunk, ChunkConfig, SearchResult
from .reranker import NoopReranker, Reranker
from .vector_store import VectorStore

logger = logging.getLogger(__name__)

# 默认值
DEFAULT_TOP_K = 20       # 粗搜条数
DEFAULT_FINAL_K = 5      # 最终返回条数
DEFAULT_SCORE_THRESHOLD = 0.4  # 相似度阈值
COLD_START_BATCH = 1000  # 冷启动每批处理条数
COLD_START_DAYS = 30     # 冷启动回溯天数
STATE_FILE = "data/rag_state.json"  # 索引进度持久化


class RAGEngine:
    """RAG 编排引擎。

    用法：
        rag = RAGEngine(store, embedder, chunker)
        rag.warmup()
        rag.ingest_one(msg)          # 增量索引
        results = rag.search(query)  # 检索
        context = rag.build_context(results)  # 拼上下文
    """

    def __init__(self, store: VectorStore, embedder: Embedder,
                 chunker: Optional[chunking_mod.ChunkingStrategy] = None,
                 reranker: Optional[Reranker] = None):
        self._store = store
        self._embedder = embedder
        self._chunker = chunker or chunking_mod.SlidingWindowChunker()
        self._reranker = reranker or NoopReranker()

        # 增量 buffer：按 chat_id 维护，凑够 window 条才索引
        self._msg_buffer: dict[str, list[dict]] = {}
        self._buffer_lock = threading.Lock()

        # 索引进度
        self._last_indexed_id = 0
        self._state_path = Path(STATE_FILE)

    # ── 生命周期 ──────────────────────────────────────────────────

    def warmup(self):
        """启动时调用。加载模型 + 初始化 ChromaDB。"""
        self._embedder.warmup()
        self._store.warmup()  # ChromaStore 的 warmup
        self._reranker.warmup()
        self._load_state()
        logger.info("[RAG] RAGEngine 就绪: dim=%d", self._embedder.dim)

    def close(self):
        """关闭时调用。释放资源。"""
        self._store.close()
        logger.info("[RAG] RAGEngine 已关闭")

    # ── 索引 ──────────────────────────────────────────────────────

    def ingest(self, messages: list[dict], source: str = "msg"):
        """批量索引。用于冷启动。

        Args:
            messages: 原始消息列表
            source: 数据源标识
        """
        if not messages:
            return

        try:
            chunks = self._chunker.chunk(messages, source=source)
            if not chunks:
                return

            texts = [c.content for c in chunks]
            embeddings = self._embedder.encode(texts)
            self._store.add(chunks, embeddings)
        except Exception as e:
            logger.warning("[RAG] ingest 批量索引失败: %s", e)

    def ingest_one(self, msg: dict, source: str = "msg"):
        """增量索引单条消息。

        消息先进入 buffer（按 chat_id 隔离），凑够 window 条才索引。

        Args:
            msg: 单条消息
            source: 数据源标识
        """
        # 过滤无意义内容
        content = msg.get("content", "").strip()
        if not content:
            return
        if len(content) < 2:
            # 单字/表情包不索引
            return

        chat_id = str(msg.get("chat_id", "__unknown__"))

        # buffer 操作需要线程安全
        with self._buffer_lock:
            buf = self._msg_buffer.setdefault(chat_id, [])
            buf.append(msg)
            window = (self._chunker.config.window
                     if hasattr(self._chunker, 'config')
                     else 3)
            stride = (self._chunker.config.stride
                     if hasattr(self._chunker, 'config')
                     else 2)
            if len(buf) >= window:
                group = buf[-window:]
                buf[:] = buf[-(stride):]  # 保留重叠
            else:
                return  # 缓冲未满，等待下一条

        # 拼成 chunk 并索引
        try:
            chunk = self._chunker.chunk_one(msg, source, group[:-1])
            if chunk is None:
                return
            emb = self._embedder.encode([chunk.content])
            self._store.add([chunk], emb)
        except Exception as e:
            logger.warning("[RAG] ingest_one 失败: %s", e)

    # ── 检索 ──────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = DEFAULT_TOP_K,
               final_k: int = DEFAULT_FINAL_K,
               where: Optional[dict] = None) -> list[SearchResult]:
        """检索。query 文本 → 编码 → 向量搜索 → 重排序。

        Args:
            query: 用户提问
            top_k: 粗搜条数（不含 reranker 时此值即最终条数的搜索范围）
            final_k: 最终返回条数
            where: 过滤条件
        Returns:
            SearchResult 列表，已按相似度降序排序
        """
        try:
            q_emb = self._embedder.encode([query])
            # 多召一些用于过滤和重排序
            results = self._store.search(q_emb[0], top_k=top_k * 2, where=where)
            if not results:
                return []

            # 相似度阈值过滤
            results = [r for r in results
                      if r.score >= DEFAULT_SCORE_THRESHOLD]
            if not results:
                return []

            # 重排序
            results = self._reranker.rerank(query, results, top_n=final_k)
            return results

        except Exception as e:
            logger.warning("[RAG] search 失败: %s", e)
            return []

    def build_context(self, results: list[SearchResult]) -> str:
        """检索结果 → 可读文本。

        格式：
            [1] [07-01 张三] 明天下午3点开评审会
            [2] [07-01 李四] 会议室订了A301
        """
        lines = []
        for i, r in enumerate(results, 1):
            ts = r.chunk.created_at
            if len(ts) > 10:
                ts = ts[5:16]  # 取 "MM-DD HH:MM"
            sender = r.chunk.sender_name or "未知"
            lines.append(f"[{i}] [{ts} {sender}] {r.chunk.content}")

        if not lines:
            return ""

        return "\n".join(lines)

    # ── 冷启动 ────────────────────────────────────────────────────

    def cold_start(self, store, tracked_groups: Optional[list[str]] = None):
        """后台线程：批量索引已有历史消息。

        Args:
            store: MessageStore 实例（用于 SQL 查询）
            tracked_groups: 关注的群 ID 列表（配置了摘要/提醒的群）
        """
        logger.info("[RAG] 冷启动开始")
        try:
            conditions = self._build_query_conditions(tracked_groups)
            batch_size = COLD_START_BATCH

            while True:
                messages = store.get_messages(
                    limit=batch_size,
                    order="asc",
                    **conditions,
                )
                if not messages:
                    break

                self.ingest(messages, source="msg")

                # 更新进度
                self._last_indexed_id = messages[-1]["id"]
                self._save_state()
                conditions["id_gt"] = self._last_indexed_id

            logger.info("[RAG] 冷启动完成: last_id=%d", self._last_indexed_id)

        except Exception as e:
            logger.warning("[RAG] 冷启动失败: %s", e)

    def _build_query_conditions(self, tracked_groups) -> dict:
        """构造首次冷启动的查询条件。"""
        if self._last_indexed_id > 0:
            # 不是首次：从上一次进度继续
            return {"id_gt": self._last_indexed_id}

        conditions = {}
        if tracked_groups:
            conditions["chat_id_in"] = tracked_groups
        # 只索引最近 COLD_START_DAYS 天的
        cutoff = (datetime.now() - timedelta(days=COLD_START_DAYS)).isoformat()
        conditions["created_after"] = cutoff
        return conditions

    # ── 状态持久化 ─────────────────────────────────────────────────

    def _load_state(self):
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._last_indexed_id = data.get("last_indexed_msg_id", 0)
                logger.info("[RAG] 恢复索引进度: last_id=%d",
                           self._last_indexed_id)
        except Exception as e:
            logger.warning("[RAG] 状态文件读取失败: %s", e)
            self._last_indexed_id = 0

    def _save_state(self):
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({
                    "last_indexed_msg_id": self._last_indexed_id,
                    "updated_at": datetime.now().isoformat(),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("[RAG] 状态文件写入失败: %s", e)
