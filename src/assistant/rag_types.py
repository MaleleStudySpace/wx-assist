"""RAG 纯数据类定义（零外部依赖，可被无 RAG 版 EXE 安全导入）。

只有 dataclass，不依赖 numpy/chromadb/fastembed。
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Chunk:
    """一个检索单元。由分块策略从原始消息产生。

    id: 全局唯一标识 "{source}_{original_id}_part{seq}"
    source: 数据源类型 "msg" | "fav" | "sns" | "oa"
    source_id: 原始记录 ID（对应 messages 表主键）
    chat_id: 群 ID
    sender_name: 发送者昵称
    content: 拼接后的纯文本（含发送者前缀）
    created_at: ISO 格式时间
    metadata: 扩展字段，各数据源可自定义
    """
    id: str
    source: str
    source_id: str
    chat_id: str
    sender_name: str
    content: str
    created_at: str
    metadata: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    """检索结果条目，包含 chunk 和对应的相似度分数。"""
    chunk: Chunk
    score: float       # 余弦相似度，范围 [0, 1]
    rank: int = 0      # 重排后排位（未重排时为 0）


@dataclass
class ChunkConfig:
    """分块参数。可微调，上线后根据效果调整。"""
    window: int = 3      # 几条消息拼一组
    stride: int = 2      # 步长（= window - 重叠数）
    min_length: int = 2  # 至少几条才输出 chunk
