"""Embedding 模型封装。

将文本转换为向量。只有 FastEmbedder（基于 fastembed + ONNX Runtime）一种实现。
不依赖 PyTorch，只做推理不做训练。
"""

import logging
import sys
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from . import acceleration
from .. import rag_status

import numpy as np

logger = logging.getLogger(__name__)


class Embedder(ABC):
    """向量化接口。负责文本 → 向量的转换。"""

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """将文本列表编码为向量矩阵。

        Args:
            texts: 文本列表
        Returns:
            float32 向量矩阵，shape=(len(texts), dim)
        """
        ...

    @abstractmethod
    def warmup(self):
        """预加载模型资源。在 Bot 启动时调用一次。"""
        ...

    @property
    @abstractmethod
    def dim(self) -> int:
        """向量维度。"""
        ...


class FastEmbedder(Embedder):
    """基于 fastembed + ONNX Runtime 的本地 Embedding。

    模型文件已放入项目 models/ 目录，运行时从本地加载，不联网。
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5",
                 batch_size: int = 16, threads: int = 2, device: str = "auto"):
        if batch_size < 1 or threads < 1:
            raise ValueError("batch_size and threads must be positive")
        self.model_name = model_name
        self._batch_size = batch_size
        self._threads = threads
        self._requested_device = device
        self._device = "cpu"
        self._fallback_reason = ""
        self._encode_lock = threading.Lock()
        self._model = None
        self._dim = None

    def _find_cache_dir(self) -> str:
        """确定模型文件路径。

        优先顺序：
        1. 源码模式：项目根目录下的 models/
        2. EXE 模式：PyInstaller 打包路径下的 models/
        """
        # EXE 模式
        if getattr(sys, 'frozen', False):
            base = Path(sys._MEIPASS)
        else:
            # 源码模式：rag/embedder.py 向上 4 层到项目根
            base = Path(__file__).resolve().parent.parent.parent.parent

        cache_dir = base / "models"
        if cache_dir.exists():
            return str(cache_dir)

        # fallback：当前工作目录下的 models/
        fallback = Path("models")
        if fallback.exists():
            return str(fallback)

        # 默认让 fastembed 用自己的缓存
        return str(Path.home() / ".cache" / "fastembed")

    def warmup(self):
        if self._model is not None:
            return

        try:
            cache_dir = self._find_cache_dir()
            logger.info("[RAG] 加载嵌入模型: cache_dir=%s", cache_dir)
            available = acceleration.capabilities()
            self._device = acceleration.select_device(self._requested_device, available)
            if self._requested_device not in ("auto", "cpu") and self._device == "cpu":
                self._fallback_reason = "当前运行环境未包含所选显卡支持，已使用 CPU"
            try:
                self._load_model(cache_dir)
            except Exception:
                if self._device == "cpu":
                    raise
                logger.warning("[RAG] 显卡初始化失败，回退 CPU", exc_info=True)
                self._device = "cpu"
                self._fallback_reason = "显卡初始化失败，已使用 CPU"
                self._load_model(cache_dir)

            active = acceleration.active_device(self._model)
            if active != self._device:
                self._fallback_reason = "显卡后端未成功启用，已使用 CPU"
            self._device = active
            self._publish_hardware()
            logger.info("[RAG] 嵌入模型就绪: dim=%d device=%s", self._dim, self._device)

        except Exception as e:
            logger.warning("[RAG] 模型加载失败: %s", e)
            self._model = None
            raise

    def _load_model(self, cache_dir):
        self._model = acceleration.create_model(
            self.model_name, cache_dir, self._threads, self._device,
        )
        test = list(self._model.embed(["预热"], batch_size=1))
        self._dim = len(test[0])

    def _publish_hardware(self):
        rag_status.update(hardware={
            "requested": self._requested_device, "active": self._device,
            "label": acceleration.LABELS[self._device],
            "batch_size": self._batch_size, "fallback_reason": self._fallback_reason,
        })

    def encode(self, texts: list[str]) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("Embedder 未预热，请先调用 warmup()")

        try:
            # FastEmbed defaults to 256 documents per batch. Long messages can
            # consume several GB, multiplied by concurrent cache/cold-start jobs.
            with self._encode_lock:
                try:
                    vectors = list(self._model.embed(texts, batch_size=self._batch_size))
                except Exception:
                    if self._device == "cpu":
                        raise
                    logger.warning("[RAG] 显卡编码失败，改用 CPU 重试", exc_info=True)
                    self._device = "cpu"
                    self._fallback_reason = "显卡编码失败（可能显存不足），已使用 CPU"
                    self._load_model(self._find_cache_dir())
                    self._publish_hardware()
                    vectors = list(self._model.embed(texts, batch_size=self._batch_size))
            return np.array(vectors, dtype=np.float32)
        except Exception as e:
            logger.warning("[RAG] encode 失败: %s", e)
            raise

    @property
    def dim(self) -> int:
        if self._dim is None:
            raise RuntimeError("Embedder 未预热，无法获取维度")
        return self._dim
