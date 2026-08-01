"""一次性迁移脚本：将 messages.db 中历史 zstd 压缩消息解压为明文。

背景：早期版本入库链路未做 zstd 解压，微信 4.x 将大部分消息 content 存为
hex 编码的 zstd 压缩数据（魔数 28b52ffd），导致 messages.db 里混有密文。
digest / RAG / 记忆等下游消费方读到的是乱码（{{ encrypted }}）。

本脚本对存量密文逐条解压回写为明文，与入库链路（wcdb_backend._standardize）
保持同一逻辑：解压成功 → 明文；解压失败（媒体 XML 等非 zstd 内容）→ 保留原文。

幂等性：解压后内容不再是 28b52ffd 开头的 hex，脚本可重复执行，不会二次处理。

用法：
    D:\\Python313\\python.exe scripts/migrate_messages_decrypt.py
    # 指定数据库（默认 data/messages.db）
    D:\\Python313\\python.exe scripts/migrate_messages_decrypt.py --db 其他路径/messages.db
"""

import argparse
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path

# 允许以源码模式直接运行（scripts/ 在项目根，需能 import src 包）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.wechat.content_codec import decompress_content  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate_messages_decrypt")

# 与 wcdb_backend._standardize 保持一致的群聊前缀剥离（要求"前缀 + 换行"）
_GROUP_PREFIX_RE = re.compile(r'^[a-zA-Z0-9_@.\-]+:\r?\n')


def _strip_group_prefix(content: str) -> str:
    return _GROUP_PREFIX_RE.sub('', content, count=1)


def migrate(db_path: str, dry_run: bool = False) -> int:
    """迁移 messages.db：解压密文 content 回写明文。返回处理条数。"""
    if not Path(db_path).exists():
        logger.error("数据库不存在: %s", db_path)
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 统计密文总量（28b52ffd 开头的 hex）
        total = conn.execute(
            """SELECT COUNT(*) AS n FROM messages
               WHERE lower(substr(content, 1, 8)) = '28b52ffd'"""
        ).fetchone()["n"]
        logger.info("待迁移密文消息: %d 条", total)
        if total == 0:
            return 0

        # 分批拉取处理（keyset 分页：基于 rowid 游标，不用 OFFSET ——
        # 因为每批 UPDATE 会把密文改成明文，下一次 OFFSET 查询的结果集
        # 已变化，会跳过一半数据。用 WHERE rowid > last 稳定推进。）
        done = 0
        changed = 0
        failed = 0
        last_rowid = 0
        cursor = conn.cursor()
        batch_sql = """SELECT rowid, chat_id, content FROM messages
                       WHERE rowid > ? AND lower(substr(content, 1, 8)) = '28b52ffd'
                       ORDER BY rowid LIMIT ?"""

        while True:
            rows = cursor.execute(batch_sql, (last_rowid, 500)).fetchall()
            if not rows:
                break
            for row in rows:
                raw = row["content"] or ""
                text = decompress_content(raw)
                # 解压成功且内容变化 → 明文；否则保留原文（含解压失败场景）
                if text == raw:
                    failed += 1
                    last_rowid = row["rowid"]
                    continue
                # 群聊消息剥离 sender_id 前缀（与入库链路一致）
                if row["chat_id"].endswith("@chatroom"):
                    text = _strip_group_prefix(text)
                if text == raw:
                    failed += 1
                    last_rowid = row["rowid"]
                    continue
                if not dry_run:
                    conn.execute(
                        "UPDATE messages SET content = ? WHERE rowid = ?",
                        (text, row["rowid"]),
                    )
                changed += 1
                last_rowid = row["rowid"]
            done += len(rows)
            conn.commit()
            logger.info("已处理 %d/%d (变更 %d, 保留原文 %d)",
                        done, total, changed, failed)

        if dry_run:
            logger.info("[dry-run] 共 %d 条可解压为明文（未写入）", changed)
        else:
            conn.commit()
            logger.info("迁移完成: 变更 %d 条, 保留原文 %d 条", changed, failed)
        return changed
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="迁移 messages.db 历史密文为明文")
    parser.add_argument("--db", default="data/messages.db",
                        help="messages.db 路径（默认 data/messages.db）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计不解压回写（预览影响面）")
    args = parser.parse_args()

    t0 = time.time()
    changed = migrate(args.db, dry_run=args.dry_run)
    logger.info("耗时 %.1fs", time.time() - t0)
    sys.exit(0 if changed >= 0 else 1)
