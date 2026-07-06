"""Embedding 模型封装。

将文本转换为向量。只有 FastEmbedder（基于 fastembed + ONNX Runtime）一种实现。
不依赖 PyTorch，只做推理不做训练。
"""

import logging
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

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

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self.model_name = model_name
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
            from fastembed import TextEmbedding

            cache_dir = self._find_cache_dir()
            logger.info("[RAG] 加载嵌入模型: cache_dir=%s", cache_dir)

            self._model = TextEmbedding(
                model_name=self.model_name,
                cache_dir=cache_dir,
            )

            # 预热：编码一次确保 ONNX Runtime 就绪
            list(self._model.embed(["预热"]))

            # 确认维度
            test = list(self._model.embed(["d"]))
            self._dim = len(test[0])
            logger.info("[RAG] 嵌入模型就绪: dim=%d", self._dim)

        except Exception as e:
            logger.warning("[RAG] 模型加载失败: %s", e)
            self._model = None
            raise

    def encode(self, texts: list[str]) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("Embedder 未预热，请先调用 warmup()")

        try:
            vectors = list(self._model.embed(texts))
            return np.array(vectors, dtype=np.float32)
        except Exception as e:
            logger.warning("[RAG] encode 失败: %s", e)
            raise

    @property
    def dim(self) -> int:
        if self._dim is None:
            raise RuntimeError("Embedder 未预热，无法获取维度")
        return self._dim
