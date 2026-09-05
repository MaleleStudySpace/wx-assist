"""WCDB 数据本地缓存层。

缓存表（全部在 data/messages.db，WAL 模式）：
  - oa_accounts: 公众号账号列表
  - oa_cache: 公众号文章
  - sns_cache: 朋友圈
  - fav_cache: 收藏

核心原则：
  1. 新增缓存不影响原有功能（收藏嵌套聊天记录等保持原样）
  2. 写操作串行化（_write_lock），读操作无锁（WAL 支持并发读）
  3. 全量同步用 _syncing flag 防重复
  4. 异常只记 warning，不影响主流程
  5. display_name 走 _session_cache 内存缓存，无需持久化
"""

import html
import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class ContentCache:
    """WCDB 数据本地缓存。管理四张缓存表的 CRUD 和同步。"""

    def __init__(self, db_path: str = "data/messages.db"):
        self._db_path = db_path
        self._write_lock = threading.Lock()
        # 全文抓取开关（默认全开=现状）：由 bot/server 热更新注入 set_full_text_config
        self._full_text_enabled = True
        self._full_text_ignore: set[str] = set()
        # 全量同步进行中标记，防定时器重复触发
        self._syncing: dict[str, bool] = {
            "oa": False, "sns": False, "fav": False,
        }
        self._sync_lock = threading.Lock()
        # RAG 重索引状态：防止 OA/SNS/Fav 定时器 + OA Monitor 多线程并发重索引
        # _reindexing[source] = True 表示正在跑，False 表示空闲
        # _pending[source] = True 表示有新数据等待，下一次重索引会自动触发
        self._reindexing: dict[str, bool] = {"oa": False, "sns": False, "fav": False}
        self._pending: dict[str, bool] = {"oa": False, "sns": False, "fav": False}
        self._reindex_lock = threading.Lock()
        # 增量重索引：只索引进 cached_at > last_indexed_at[source] 的数据
        # 默认 0 表示全量索引（首次启动），缓存到 data/last_indexed.json 跨重启持久化
        self._last_indexed_at: dict[str, float] = self._load_index_cursor()
        self._init_tables()

    def set_full_text_config(self, enabled: bool, ignore_gh_ids: list = None) -> None:
        """热更新全文抓取开关。只影响全文抓取线程，不影响 oa_monitor 推送/摘要。"""
        self._full_text_enabled = bool(enabled)
        self._full_text_ignore = set(ignore_gh_ids or [])

    def _index_cursor_path(self) -> str:
        return "data/last_indexed.json"

    def _load_index_cursor(self) -> dict[str, float]:
        """从磁盘加载增量游标，避免每次重启全量索引。"""
        import os, json
        path = self._index_cursor_path()
        try:
            if os.path.exists(path):
                data = json.loads(open(path, encoding="utf-8").read())
                return {
                    "oa": float(data.get("oa") or 0),
                    "sns": float(data.get("sns") or 0),
                    "fav": float(data.get("fav") or 0),
                }
        except Exception as e:
            # 游标损坏被静默当"首次启动"→ 全量重索引（分钟级写入）。
            logger.warning("索引游标读取失败，将全量重索引: %s", e)
        return {"oa": 0.0, "sns": 0.0, "fav": 0.0}

    def _save_index_cursor(self):
        """持久化增量游标到磁盘（原子写入）。"""
        import json, os, tempfile
        try:
            path = self._index_cursor_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._last_indexed_at, f, ensure_ascii=False)
            os.replace(tmp, path)  # 原子替换，避免写一半崩溃
        except Exception as e:
            # 游标存不进去 → 每次重启都全量重索引且静默。磁盘故障必须可见。
            logger.warning("索引游标保存失败，重启将全量重索引: %s", e)

    # ══════════════════════════════════════════════════════════════
    # 连接管理
    # ══════════════════════════════════════════════════════════════

    def _get_conn(self) -> sqlite3.Connection:
        """每次调用新连接，WAL 模式。"""
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ══════════════════════════════════════════════════════════════
    # DDL
    # ══════════════════════════════════════════════════════════════

    def _init_tables(self):
        """创建四张缓存表 + 索引。启动时调用一次，失败则 Bot 初始化失败。

        注意：建表和建索引分两步，中间插入 _migrate_tables 自动补充缺少的列，
        避免旧数据库因缺少列导致 CREATE INDEX 失败。
        """
        table_ddl = """
        CREATE TABLE IF NOT EXISTS oa_accounts (
            gh_id           TEXT PRIMARY KEY,
            display_name    TEXT NOT NULL DEFAULT '',
            avatar_url      TEXT DEFAULT '',
            last_updated    INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS oa_cache (
            url             TEXT PRIMARY KEY,
            gh_id           TEXT NOT NULL,
            title           TEXT NOT NULL DEFAULT '',
            digest          TEXT NOT NULL DEFAULT '',
            cover_url       TEXT DEFAULT '',
            source_name     TEXT NOT NULL DEFAULT '',
            pub_time        INTEGER DEFAULT 0,
            full_content    TEXT DEFAULT '',
            content_status  INTEGER DEFAULT 0,
            llm_summary     TEXT DEFAULT '',
            llm_summary_ok  INTEGER DEFAULT 0,
            cached_at       INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sns_cache (
            post_id         TEXT PRIMARY KEY,
            username        TEXT NOT NULL,
            nickname        TEXT DEFAULT '',
            clean_content   TEXT DEFAULT '',
            create_time     INTEGER DEFAULT 0,
            like_count      INTEGER DEFAULT 0,
            comment_count   INTEGER DEFAULT 0,
            location_name   TEXT DEFAULT '',
            media_json      TEXT DEFAULT '',
            likes_json      TEXT DEFAULT '',
            comments_json   TEXT DEFAULT '',
            raw_xml         TEXT DEFAULT '',
            cached_at       INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS fav_cache (
            fav_id           INTEGER PRIMARY KEY,
            type             INTEGER DEFAULT 0,
            type_name        TEXT DEFAULT '',
            title            TEXT DEFAULT '',
            description      TEXT DEFAULT '',
            link             TEXT DEFAULT '',
            from_user        TEXT DEFAULT '',
            update_time      INTEGER DEFAULT 0,
            chat_records_json TEXT DEFAULT '',
            media_json       TEXT DEFAULT '',
            clean_text       TEXT DEFAULT '',
            cached_at        INTEGER NOT NULL
        );
        """
        index_ddl = """
        CREATE INDEX IF NOT EXISTS idx_oa_gh_id ON oa_cache(gh_id);
        CREATE INDEX IF NOT EXISTS idx_oa_pub_time ON oa_cache(pub_time);
        CREATE INDEX IF NOT EXISTS idx_sns_username ON sns_cache(username);
        CREATE INDEX IF NOT EXISTS idx_sns_create_time ON sns_cache(create_time);
        CREATE INDEX IF NOT EXISTS idx_fav_type ON fav_cache(type);
        CREATE INDEX IF NOT EXISTS idx_fav_update_time ON fav_cache(update_time);
        """
        conn = self._get_conn()
        try:
            # 第 1 步：建表（旧表已存在则跳过）
            conn.executescript(table_ddl)
            conn.commit()
            conn.close()

            # 第 2 步：自动补充旧表缺少的列（内部自己管理连接）
            self._migrate_tables()

            # 第 3 步：建索引（此时所有列已就绪）
            conn = self._get_conn()
            conn.executescript(index_ddl)
            conn.commit()
            logger.info("[CACHE] 四张缓存表已就绪")
        except Exception as e:
            logger.warning("[CACHE] 建表/建索引失败: %s", e)
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ── 自动表迁移 ────────────────────────────────────────────────
    # 每次启动时检查并补充旧数据库可能缺少的列。
    # 方法：对比 DDL 定义的目标列和 PRAGMA table_info 的实际列，
    # 缺少的列自动 ALTER TABLE ADD COLUMN。
    # 后续 DDL 新增列后会自动迁移，无需手动维护 migration 列表。

    @staticmethod
    def _parse_ddl_columns(ddl: str) -> dict[str, list[tuple[str, str]]]:
        """从 CREATE TABLE DDL 中提取每张表的列定义。
        Returns:
            {table_name: [(col_name, col_def), ...]}
        """
        tables = {}
        # 匹配 CREATE TABLE IF NOT EXISTS xxx ( ... );
        pattern = re.compile(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\("
            r"(.*?)\);",
            re.DOTALL | re.IGNORECASE,
        )
        for m in pattern.finditer(ddl):
            tname = m.group(1)
            body = m.group(2)
            cols = []
            for line in body.split(","):
                line = line.strip()
                if not line or line.upper().startswith("PRIMARY KEY"):
                    continue
                parts = line.split(None, 1)
                if parts and not parts[0].upper().startswith(("INDEX", "FOREIGN", "CONSTRAINT")):
                    col_name = parts[0].strip()
                    col_def = parts[1].strip() if len(parts) > 1 else ""
                    cols.append((col_name, col_def))
            tables[tname] = cols
        return tables

    def _migrate_tables(self):
        """自动迁移：用 PRAGMA table_info 对比 DDL，补充旧库缺少的列。"""
        ddl = """
        CREATE TABLE IF NOT EXISTS oa_accounts (
            gh_id           TEXT PRIMARY KEY,
            display_name    TEXT NOT NULL DEFAULT '',
            avatar_url      TEXT DEFAULT '',
            last_updated    INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS oa_cache (
            url             TEXT PRIMARY KEY,
            gh_id           TEXT NOT NULL,
            title           TEXT NOT NULL DEFAULT '',
            digest          TEXT NOT NULL DEFAULT '',
            cover_url       TEXT DEFAULT '',
            source_name     TEXT NOT NULL DEFAULT '',
            pub_time        INTEGER DEFAULT 0,
            full_content    TEXT DEFAULT '',
            content_status  INTEGER DEFAULT 0,
            llm_summary     TEXT DEFAULT '',
            llm_summary_ok  INTEGER DEFAULT 0,
            cached_at       INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sns_cache (
            post_id         TEXT PRIMARY KEY,
            username        TEXT NOT NULL,
            nickname        TEXT DEFAULT '',
            clean_content   TEXT DEFAULT '',
            create_time     INTEGER DEFAULT 0,
            like_count      INTEGER DEFAULT 0,
            comment_count   INTEGER DEFAULT 0,
            location_name   TEXT DEFAULT '',
            media_json      TEXT DEFAULT '',
            likes_json      TEXT DEFAULT '',
            comments_json   TEXT DEFAULT '',
            raw_xml         TEXT DEFAULT '',
            cached_at       INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS fav_cache (
            fav_id           INTEGER PRIMARY KEY,
            type             INTEGER DEFAULT 0,
            type_name        TEXT DEFAULT '',
            title            TEXT DEFAULT '',
            description      TEXT DEFAULT '',
            link             TEXT DEFAULT '',
            from_user        TEXT DEFAULT '',
            update_time      INTEGER DEFAULT 0,
            chat_records_json TEXT DEFAULT '',
            media_json       TEXT DEFAULT '',
            clean_text       TEXT DEFAULT '',
            cached_at        INTEGER NOT NULL
        );
        """
        target_cols = self._parse_ddl_columns(ddl)

        conn = self._get_conn()
        try:
            for table, desired in target_cols.items():
                # 获取当前表的列
                try:
                    existing = {
                        r[1]
                        for r in conn.execute(
                            "PRAGMA table_info(%s)" % table
                        ).fetchall()
                    }
                except sqlite3.OperationalError:
                    continue  # 表不存在，跳过
                for col_name, col_def in desired:
                    if col_name not in existing:
                        sql = "ALTER TABLE %s ADD COLUMN %s %s" % (
                            table, col_name, col_def
                        )
                        conn.execute(sql)
                        logger.info(
                            "[CACHE] 迁移: %s.%s 列已添加", table, col_name
                        )
            conn.commit()
        except Exception as e:
            logger.warning("[CACHE] 表迁移失败: %s", e)
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════
    # 读写操作
    # ══════════════════════════════════════════════════════════════

    def query(self, sql: str, params=None) -> list[sqlite3.Row]:
        """读操作，无锁。"""
        conn = self._get_conn()
        try:
            if params:
                return conn.execute(sql, params).fetchall()
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def query_one(self, sql: str, params=None) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def upsert(self, table: str, data: dict):
        """单条写，INSERT OR REPLACE，有锁。"""
        with self._write_lock:
            conn = self._get_conn()
            try:
                cols = ", ".join(data.keys())
                ph = ", ".join("?" for _ in data)
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({ph})",
                    list(data.values()),
                )
                conn.commit()
            except Exception as e:
                logger.warning("[CACHE] upsert %s 失败: %s", table, e)
            finally:
                conn.close()

    def batch_upsert(self, table: str, records: list[dict]):
        """批量写，事务内 executemany，有锁。"""
        if not records:
            return
        with self._write_lock:
            conn = self._get_conn()
            try:
                cols = ", ".join(records[0].keys())
                ph = ", ".join("?" for _ in records[0])
                conn.executemany(
                    f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({ph})",
                    [list(r.values()) for r in records],
                )
                conn.commit()
                logger.info("[CACHE] batch_upsert %s: %d 行", table, len(records))
            except Exception as e:
                logger.warning("[CACHE] batch_upsert %s 失败 (%d 行): %s",
                               table, len(records), e)
            finally:
                conn.close()

    def update(self, table: str, data: dict, where: dict):
        """按条件更新指定列，有锁。

        用于只更新部分字段的场景（如保存 LLM 摘要），
        避免 INSERT OR REPLACE 的 NOT NULL 约束问题。

        Args:
            table: 表名
            data: 要更新的列 {col: value}
            where: 筛选条件 {col: value}
        """
        if not data or not where:
            return
        with self._write_lock:
            conn = self._get_conn()
            try:
                set_clause = ", ".join(f"{k}=?" for k in data)
                where_clause = " AND ".join(f"{k}=?" for k in where)
                conn.execute(
                    f"UPDATE {table} SET {set_clause} WHERE {where_clause}",
                    list(data.values()) + list(where.values()),
                )
                conn.commit()
            except Exception as e:
                logger.warning("[CACHE] update %s 失败: %s", table, e)
            finally:
                conn.close()

    # ══════════════════════════════════════════════════════════════
    # 全量同步控制（_syncing flag）
    # ══════════════════════════════════════════════════════════════

    def _try_start_full_sync(self, source: str) -> bool:
        """尝试获取全量同步执行权。返回 True 表示可以开始。"""
        with self._sync_lock:
            if self._syncing.get(source, False):
                logger.info("[CACHE] %s 全量同步已在进行中，跳过", source.upper())
                return False
            self._syncing[source] = True
            return True

    def _end_full_sync(self, source: str):
        with self._sync_lock:
            self._syncing[source] = False

    def _is_full_syncing(self, source: str) -> bool:
        with self._sync_lock:
            return self._syncing.get(source, False)

    # ══════════════════════════════════════════════════════════════
    # OA 账号同步（30min 定时器）
    # ══════════════════════════════════════════════════════════════

    def sync_oa_accounts(self, client, task_center=None) -> bool:
        """增量刷新 OA 账号列表。30min 定时器调用。"""
        tid = _create_task(task_center, "cache_oa_accounts", "", "OA账号同步")
        try:
            self._sync_oa_accounts(client)
            _complete_task(task_center, tid, "OA 账号同步完成")
            return True
        except Exception as e:
            logger.warning("[CACHE] OA 账号同步失败: %s", e)
            _fail_task(task_center, tid, str(e))
            return False

    # ══════════════════════════════════════════════════════════════
    # OA 全量同步
    # ══════════════════════════════════════════════════════════════

    def sync_oa_all(self, wcdb_client, task_center=None):
        """全量同步 OA 账号 + 文章。后台线程调用。"""
        if not self._try_start_full_sync("oa"):
            return
        tid = _create_task(task_center, "cache_oa", "", "OA全量同步")
        try:
            self._sync_oa_accounts(wcdb_client)
            self._sync_oa_articles_full(wcdb_client, tid, task_center)
            _complete_task(task_center, tid, "OA 全量同步完成")
        except Exception as e:
            logger.warning("[CACHE] OA 全量同步失败: %s", e)
            _fail_task(task_center, tid, str(e))
        finally:
            self._end_full_sync("oa")

    def _sync_oa_accounts(self, client):
        """同步 OA 账号列表。

        过滤规则：get_display_names() 返回 gh_id 本身的号不入库。
        这些号在 contact 表中无 nick_name 记录（已取关或从未收到推送），
        显示 gh_id 对用户无意义，且 WCDB 中 0 篇文章，无法搜索/摘要/监控。
        后续 contact 表有数据后，下次同步自动恢复（30min 刷新，自愈）。
        """
        try:
            from src.assistant.oa_parser import get_oa_sessions
            sessions = get_oa_sessions(client)
        except Exception as e:
            logger.warning("[CACHE] get_oa_sessions 失败: %s", e)
            return
        if not sessions:
            return
        try:
            usernames = [s["username"] for s in sessions if s.get("username")]
            names = client.get_display_names(usernames) if usernames else {}
        except Exception:
            names = {}
        accounts = []
        skipped = []
        now = int(time.time())
        for s in sessions:
            uid = s.get("username", "")
            if not uid:
                continue
            display_name = names.get(uid, uid) or uid
            # 过滤：display_name == uid 说明 contact 表无 nick_name，
            # 该公众号已取关或从未互动，显示 gh_id 对用户无意义
            if display_name == uid:
                skipped.append(uid)
                continue
            accounts.append({
                "gh_id": uid,
                "display_name": display_name,
                "avatar_url": "",
                "last_updated": now,
            })
        if accounts:
            self.batch_upsert("oa_accounts", accounts)
            names_str = ", ".join(a.get("display_name", a["gh_id"]) for a in accounts[:5])
            if len(accounts) > 5:
                names_str += f" ... 共 {len(accounts)} 个"
            logger.info("[CACHE] OA 账号同步: %s (跳过 %d 个无昵称)",
                        names_str, len(skipped))

    def _sync_oa_articles_full(self, client, task_id=None, task_center=None):
        """全量同步 OA 文章：遍历每个 gh_id，拉最新 50 篇。"""
        accounts = self.query("SELECT gh_id, display_name FROM oa_accounts")
        if not accounts:
            logger.info("[CACHE] OA 文章全量跳过：无 OA 账号")
            return
        total_new = 0
        for i, row in enumerate(accounts):
            gh_id = row["gh_id"]
            name = row["display_name"] or gh_id
            try:
                new = self._sync_oa_gh(client, gh_id)
                total_new += new
            except Exception as e:
                logger.warning("[CACHE] OA 文章同步 %s 失败: %s", name, e)
            if task_id and (i + 1) % 5 == 0:
                _update_task(task_center, task_id,
                             f"第 {i+1}/{len(accounts)} 个公众号")
        if total_new:
            logger.info("[CACHE] OA 全量同步完成: %d 个公众号, %d 篇文章",
                        len(accounts), total_new)

    def _sync_oa_gh(self, client, gh_id: str) -> int:
        """同步单个 OA 公众号的文章。返回新增条数。

        全量拉取该号 WCDB 中所有文章（limit=None 内部按每页 50 分页 + 容错降级：
        任一分页失败返回已拉到的数据，不影响其他公众号）。
        """
        from src.assistant.oa_parser import fetch_oa_articles, OAArticle
        articles = fetch_oa_articles(client, gh_id, limit=None)
        if not articles:
            return 0
        # 去重：只取缓存中没有的
        existing = self._get_existing_oa_urls()
        new = []
        for a in articles:
            if a.url not in existing:
                cleaned = self._clean_oa(a)
                if cleaned:
                    new.append(cleaned)
        if new:
            self.batch_upsert("oa_cache", new)
            logger.debug("[CACHE] OA 增量 %s: 新增 %d 篇", gh_id, len(new))
        return len(new)

    def _get_existing_oa_urls(self) -> set:
        rows = self.query("SELECT url FROM oa_cache")
        return {r["url"] for r in rows}

    @staticmethod
    def _clean_oa(article) -> dict | None:
        """清洗 OA 文章。"""
        url = (article.url or "").strip()
        title = article.title or ""
        if not url or not title:
            return None
        import html, re
        title = html.unescape(title).strip()
        title = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', title)
        if not title:
            return None
        digest = html.unescape(article.digest or "").strip()
        digest = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', digest)[:500]
        return {
            "url": url,
            "gh_id": article.gh_id,
            "title": title,
            "digest": digest,
            "cover_url": (article.cover or "").strip(),
            "source_name": html.unescape(article.source_name or "").strip() or article.gh_id,
            "pub_time": article.pub_time or article.timestamp or 0,
            "full_content": "",
            "content_status": 0,
            "llm_summary": "",
            "llm_summary_ok": 0,
            "cached_at": int(time.time()),
        }

    # ══════════════════════════════════════════════════════════════
    # OA 增量合并（定时器 + 用户访问触发）
    # ══════════════════════════════════════════════════════════════

    def sync_oa_single(self, client, gh_id: str, task_center=None) -> int:
        """增量同步单个公众号的文章。返回新增条数。

        由 API 触发器调用，轻量级操作（只拉最新 10 篇），
        不检查 _syncing flag（增量合并无冲突风险）。
        """
        try:
            return self._sync_oa_gh(client, gh_id)
        except Exception as e:
            logger.warning("[CACHE] sync_oa_single %s 失败: %s", gh_id, e)
            return 0

    def sync_oa_incremental(self, client, task_center=None) -> int:
        """增量合并 OA 文章。定时器 60s + 用户访问触发。
        Returns:
            int: 新增文章数，0 表示无新内容。
        """
        if self._is_full_syncing("oa"):
            logger.debug("[CACHE] OA 全量进行中，增量跳过")
            return 0
        if client is None:
            logger.warning("[CACHE] WCDB 不可用, OA 同步跳过")
            return 0
        accounts = self.query("SELECT gh_id, display_name FROM oa_accounts")
        if not accounts:
            return 0
        total = 0
        all_new_titles = []
        tid = None
        try:
            existing = self._get_existing_oa_urls()
            for row in accounts:
                gh_id = row["gh_id"]
                name = row["display_name"] or gh_id
                try:
                    from src.assistant.oa_parser import fetch_oa_articles
                    articles = fetch_oa_articles(client, gh_id, limit=10)
                    new = []
                    for a in articles:
                        if a.url not in existing:
                            cleaned = self._clean_oa(a)
                            if cleaned:
                                new.append(cleaned)
                                existing.add(a.url)
                    if new:
                        self.batch_upsert("oa_cache", new)
                        total += len(new)
                        all_new_titles.extend(c.get("title", "") for c in new if c.get("title"))
                except Exception as e:
                    logger.warning("[CACHE] OA 增量 %s 失败: %s", name, e)
            if total:
                title_summary = " | ".join(t[:30] for t in all_new_titles[:10])
                logger.info("[CACHE] OA 增量合并: %d 篇 | %s", total, title_summary)
                tid = _create_task(task_center, "cache_oa_incremental", "", "OA增量同步")
                _complete_task(task_center, tid, f"OA 增量: 新增 {total} 篇")
        except Exception as e:
            logger.warning("[CACHE] OA 增量合并失败: %s", e)
            if tid:
                _fail_task(task_center, tid, str(e))
        return total

    # ══════════════════════════════════════════════════════════════
    # OA 全文抓取队列
    # ══════════════════════════════════════════════════════════════

    def start_oa_content_fetcher(self, task_center=None):
        """启动 OA 全文抓取后台线程。每秒 1 篇，失败标记 -1 不重试。

        Args:
            task_center: 可选，用于创建 cache_oa_content 任务追踪。
        """
        self._fetcher_tc = task_center
        self._fetcher_count = 0
        self._fetcher_task_id = None
        self._fetcher_retries: dict[str, int] = {}  # url → 连续失败次数（防卡队列）

        def _loop():
            while True:
                try:
                    self._fetch_one_content()
                except Exception as e:
                    logger.debug("[CACHE] OA 全文抓取循环异常: %s", e)
                time.sleep(2)  # 2 秒 1 篇，避免被封
        t = threading.Thread(target=_loop, daemon=True, name="oa-content-fetch")
        t.start()
        logger.info("[CACHE] OA 全文抓取队列已启动")

    def _fetch_one_content(self):
        """抓取一篇待抓取的文章全文。"""
        # 总开关关闭 → 不抓取（线程空闲）
        if not self._full_text_enabled:
            return
        # 忽略列表 → 查询排除这些公众号
        _sql = "SELECT url, title FROM oa_cache WHERE content_status=0"
        _params: list = []
        if self._full_text_ignore:
            _sql += " AND gh_id NOT IN ({})".format(
                ",".join("?" * len(self._full_text_ignore)))
            _params = list(self._full_text_ignore)
        _sql += " LIMIT 1"
        row = self.query_one(_sql, _params)
        if not row:
            # 没有待抓取文章时，重置任务状态
            if self._fetcher_task_id:
                _complete_task(self._fetcher_tc, self._fetcher_task_id,
                               f"抓取完成: {self._fetcher_count} 篇")
                self._fetcher_task_id = None
                # 追加索引完成日志：明确这是正常收敛，不是异常反复
                logger.info("[CACHE] RAG OA 原文追加索引完成: 共追加 %d 篇全文（RAG 已含全文，此后仅新文章触发索引）",
                            self._fetcher_count)
                self._fetcher_count = 0
            return

        # 首次有文章时创建任务
        if not self._fetcher_task_id and self._fetcher_tc:
            self._fetcher_task_id = _create_task(
                self._fetcher_tc, "cache_oa_content", "", "OA全文抓取"
            )
            # 追加索引开始日志：明确首次全量同步的预期行为
            try:
                _pending = self.query_one(
                    "SELECT COUNT(*) AS c FROM oa_cache WHERE content_status=0"
                )["c"]
                logger.info(
                    "[CACHE] RAG OA 原文追加索引开始: %d 篇待抓取全文（每 2 秒 1 篇，期间 "
                    "抓取成功会刷新 cached_at 触发 RAG 增量索引，表现为连续 "
                    "'RAG 索引 OA 文章: N 条'，属正常追加，抓完自动停止）",
                    _pending,
                )
            except Exception:
                pass

        url = row["url"]
        title = row["title"]
        try:
            from src.assistant.oa_reader import fetch_article_content
            content = fetch_article_content(url, timeout=15, title=title)
            if content:
                import html, re
                content = html.unescape(content)
                content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', content)
                self.update("oa_cache", {
                    "full_content": content[:50000],  # 最长 5 万字
                    "content_status": 1,
                    "cached_at": int(time.time()),  # 触及时戳触发增量重索引
                }, {"url": url})
                self._fetcher_count += 1
                self._fetcher_retries.pop(url, None)  # 成功后清除重试计数
                # 每 10 篇打一次追加索引进度日志
                if self._fetcher_count % 10 == 0:
                    try:
                        _pending = self.query_one(
                            "SELECT COUNT(*) AS c FROM oa_cache WHERE content_status=0"
                        )["c"]
                        logger.info(
                            "[CACHE] 公众号原文 本地缓存中: 已处理 %d 篇，剩余 %d 篇待抓",
                            self._fetcher_count, _pending,
                        )
                    except Exception:
                        pass
                # 每 5 篇更新一次任务进度
                if self._fetcher_count % 5 == 0 and self._fetcher_task_id:
                    _update_task(self._fetcher_tc, self._fetcher_task_id,
                                 f"已抓取 {self._fetcher_count} 篇")
            else:
                self.update("oa_cache", {"content_status": -1}, {"url": url})
        except Exception as e:
            # 403/429 直接标记失败；其他异常连续失败 ≥5 次也放弃（防无限重试卡队列）
            resp_err = getattr(e, "response", None)
            status = getattr(resp_err, "status_code", 0) if resp_err else 0
            if status in (403, 429):
                logger.warning("[CACHE] OA 全文抓取失败 %s (HTTP %d)", url, status)
                self.update("oa_cache", {"content_status": -1}, {"url": url})
            else:
                self._fetcher_retries[url] = self._fetcher_retries.get(url, 0) + 1
                if self._fetcher_retries[url] >= 5:
                    logger.warning("[CACHE] OA 全文抓取放弃 %s — 连续失败 %d 次: %s",
                                   url, self._fetcher_retries[url], e)
                    self.update("oa_cache", {"content_status": -1}, {"url": url})
                    self._fetcher_retries.pop(url, None)
                else:
                    logger.debug("[CACHE] OA 全文抓取重试 %s (%d/5): %s",
                                 url, self._fetcher_retries[url], e)

    # ══════════════════════════════════════════════════════════════
    # SNS 全量同步 + 增量合并
    # ══════════════════════════════════════════════════════════════

    def sync_sns_all(self, client, task_center=None):
        """全量同步朋友圈。限制最近 500 条。"""
        if not self._try_start_full_sync("sns"):
            return
        tid = _create_task(task_center, "cache_sns", "", "朋友圈全量同步")
        try:
            max_pages = 25  # 25 页 × 20 条 = 500
            total = 0
            existing = self._get_existing_sns_ids()
            for page in range(max_pages):
                posts = client.get_sns_timeline(limit=20, offset=page * 20)
                if not posts:
                    break
                new = []
                for p in posts:
                    pid = p.get("tid") or p.get("id")
                    if not pid or str(pid) in existing:
                        continue
                    cleaned = self._clean_sns(p)
                    if cleaned:
                        new.append(cleaned)
                        existing.add(str(pid))
                if new:
                    self.batch_upsert("sns_cache", new)
                    total += len(new)
                if tid and (page + 1) % 5 == 0:
                    _update_task(task_center, tid, f"第 {page+1}/{max_pages} 页")
            logger.info("[CACHE] 朋友圈全量同步完成: %d 条", total)
            _complete_task(task_center, tid, f"朋友圈全量: {total} 条")
        except Exception as e:
            logger.warning("[CACHE] 朋友圈全量同步失败: %s", e)
            _fail_task(task_center, tid, str(e))
        finally:
            self._end_full_sync("sns")

    def sync_sns_incremental(self, client, task_center=None) -> int:
        """增量合并朋友圈：只拉第 1 页。
        Returns:
            int: 新增朋友圈条数，0 表示无新内容。
        """
        if self._is_full_syncing("sns"):
            return 0
        if client is None:
            logger.warning("[CACHE] WCDB 不可用, SNS 增量同步跳过")
            return 0
        tid = None
        try:
            posts = client.get_sns_timeline(limit=20, offset=0)
            if not posts:
                return 0
            existing = self._get_existing_sns_ids()
            new = []
            for p in posts:
                pid = p.get("tid") or p.get("id")
                if not pid or str(pid) in existing:
                    continue
                cleaned = self._clean_sns(p)
                if cleaned:
                    new.append(cleaned)
            if new:
                self.batch_upsert("sns_cache", new)
                logger.info("[CACHE] 朋友圈增量合并: 新增 %d 条", len(new))
                tid = _create_task(task_center, "cache_sns_incremental", "", "朋友圈增量")
                _complete_task(task_center, tid, f"朋友圈增量: {len(new)} 条")
        except Exception as e:
            logger.warning("[CACHE] 朋友圈增量合并失败: %s", e)
            if tid:
                _fail_task(task_center, tid, str(e))
            return 0
        return len(new)

    def _get_existing_sns_ids(self) -> set:
        rows = self.query("SELECT post_id FROM sns_cache")
        return {r["post_id"] for r in rows}

    @staticmethod
    def _clean_sns(raw: dict) -> dict | None:
        """清洗朋友圈。优先取 contentDesc（已解码明文），无则 hex 解码。"""
        post_id = raw.get("tid") or raw.get("id")
        if not post_id:
            return None

        # contentDesc = 已解码明文，content/messageContent = hex 编码
        clean_content = ""
        desc = raw.get("contentDesc", "") or ""
        if desc.strip():
            clean_content = desc.strip()[:2000]
            import html, re
            clean_content = html.unescape(clean_content)
            clean_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', clean_content)
        else:
            content_hex = raw.get("content") or raw.get("messageContent") or ""
            if content_hex and content_hex != "0":
                try:
                    raw_bytes = bytes.fromhex(content_hex)
                    clean_content = raw_bytes.decode("utf-8", errors="replace")
                    import html, re
                    clean_content = html.unescape(clean_content)
                    clean_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', clean_content)
                    clean_content = clean_content.strip()[:2000]
                except Exception:
                    pass
        media_list = raw.get("media") or []
        return {
            "post_id": str(post_id),
            "username": str(raw.get("username", "")),
            "nickname": str(raw.get("nickname", "")),
            "clean_content": clean_content,
            "create_time": int(raw.get("createTime") or raw.get("create_time", 0)),
            "like_count": int(raw.get("likeCount", 0)),
            "comment_count": int(raw.get("commentCount", 0)),
            "location_name": str(raw.get("locationName") or raw.get("location", "") or ""),
            "media_json": json.dumps(media_list, ensure_ascii=True) if media_list else "",
            "likes_json": json.dumps(raw.get("likes", []), ensure_ascii=True),
            "comments_json": json.dumps(raw.get("comments", []), ensure_ascii=True),
            "raw_xml": str(raw.get("rawXml", "")),
            "cached_at": int(time.time()),
        }

    # ══════════════════════════════════════════════════════════════
    # 收藏全量同步 + 增量合并
    # ══════════════════════════════════════════════════════════════

    def sync_fav_all(self, client, task_center=None):
        """全量同步收藏。限制最近 1000 条。"""
        if not self._try_start_full_sync("fav"):
            return
        tid = _create_task(task_center, "cache_fav", "", "收藏全量同步")
        total = 0
        max_pages = 5
        try:
            from src.wechat.wcdb_fav_reader import WcdbFavReader
            reader = WcdbFavReader(client)
            existing = self._get_existing_fav_ids()
            for page in range(max_pages):
                offset = page * 200
                items = None
                try:
                    items = reader.get_items(limit=200, offset=offset) or []
                except Exception:
                    logger.warning("[CACHE] fav batch 失败，降级逐条加载 (page=%d)", page)
                    # 降级：元数据 + 逐条 content
                    try:
                        meta_sql = (
                            f"SELECT local_id, type, update_time, fromusr "
                            f"FROM fav_db_item ORDER BY update_time DESC "
                            f"LIMIT 200 OFFSET {offset}"
                        )
                        rows = reader._exec(meta_sql) or []
                    except Exception:
                        break
                    items = []
                    for r in rows:
                        try:
                            item = reader._parse_fav_row(r)
                            lid = r["local_id"]
                            try:
                                c_sql = f"SELECT content FROM fav_db_item WHERE local_id={lid}"
                                c_rows = reader._exec(c_sql) or []
                                if c_rows and c_rows[0].get("content"):
                                    item["content_raw"] = c_rows[0]["content"]
                                    if c_rows[0]["content"].strip().startswith("<favitem"):
                                        item.update(reader._parse_fav_xml(c_rows[0]["content"]))
                            except Exception:
                                pass
                            items.append(item)
                        except Exception:
                            pass
                if not items:
                    break
                new = []
                for item in items:
                    fid = item.get("local_id")
                    if not fid or fid in existing:
                        continue
                    cleaned = self._clean_fav(item)
                    if cleaned:
                        new.append(cleaned)
                        existing.add(fid)
                if new:
                    self.batch_upsert("fav_cache", new)
                    total += len(new)
            logger.info("[CACHE] 收藏全量同步完成: %d 条", total)
            _complete_task(task_center, tid, f"收藏全量: {total} 条")
        except Exception as e:
            logger.warning("[CACHE] 收藏全量同步失败: %s", e)
            _fail_task(task_center, tid, str(e))
        finally:
            self._end_full_sync("fav")

    def sync_fav_incremental(self, client, task_center=None) -> int:
        """增量合并收藏。
        Returns:
            int: 新增收藏条数，0 表示无新内容。
        """
        if self._is_full_syncing("fav"):
            return 0
        tid = None
        new_count = 0
        try:
            from src.wechat.wcdb_fav_reader import WcdbFavReader
            reader = WcdbFavReader(client)
            # 批量读 + 降级（内联）
            items = None
            try:
                items = reader.get_items(limit=200, offset=0) or []
            except Exception:
                logger.warning("[CACHE] fav 增量 batch 失败，降级")
                try:
                    meta_sql = "SELECT local_id, type, update_time, fromusr FROM fav_db_item ORDER BY update_time DESC LIMIT 200"
                    rows = reader._exec(meta_sql) or []
                except Exception:
                    return 0
                items = []
                for r in rows:
                    try:
                        item = reader._parse_fav_row(r)
                        lid = r["local_id"]
                        try:
                            c_sql = f"SELECT content FROM fav_db_item WHERE local_id={lid}"
                            c_rows = reader._exec(c_sql) or []
                            if c_rows and c_rows[0].get("content"):
                                item["content_raw"] = c_rows[0]["content"]
                                if c_rows[0]["content"].strip().startswith("<favitem"):
                                    item.update(reader._parse_fav_xml(c_rows[0]["content"]))
                        except Exception:
                            pass
                        items.append(item)
                    except Exception:
                        pass
            if not items:
                return 0
            max_cached = self._get_max_fav_id()
            new = []
            for item in items:
                fid = item.get("local_id")
                try:
                    fid = int(fid)
                except (ValueError, TypeError):
                    continue
                if fid > max_cached:
                    cleaned = self._clean_fav(item)
                    if cleaned:
                        new.append(cleaned)
            if new:
                self.batch_upsert("fav_cache", new)
                new_count = len(new)
                logger.info("[CACHE] 收藏增量合并: 新增 %d 条", new_count)
                tid = _create_task(task_center, "cache_fav_incremental", "", "收藏增量")
                _complete_task(task_center, tid, f"收藏增量: {new_count} 条")
        except Exception as e:
            logger.warning("[CACHE] 收藏增量合并失败: %s", e)
            if tid:
                _fail_task(task_center, tid, str(e))
            return 0
        return new_count

    def _get_existing_fav_ids(self) -> set:
        rows = self.query("SELECT fav_id FROM fav_cache")
        return {r["fav_id"] for r in rows}

    def _get_max_fav_id(self) -> int:
        row = self.query_one("SELECT MAX(fav_id) AS m FROM fav_cache")
        return row["m"] if row and row["m"] else 0

    @staticmethod
    def _clean_fav(item: dict) -> dict | None:
        """清洗收藏。保留完整 chat_records 用于 Web UI，展平 clean_text 用于 RAG。"""
        fav_id = item.get("local_id")
        if not fav_id:
            logger.warning("[CACHE] 跳过无效收藏: local_id 为空")
            return None
        import html, re
        title = html.unescape(item.get("title", "")).strip()
        title = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', title)
        description = html.unescape(item.get("description", "")).strip()
        description = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', description)
        link = (item.get("link") or "").strip()

        # 完整保留 chat_records 供 Web UI 展示
        chat_records = item.get("chat_records", [])
        chat_records_json = json.dumps(chat_records, ensure_ascii=True) if chat_records else ""

        # 展平 clean_text 用于 RAG 索引（含嵌套的聊天记录内容）
        clean_parts = []
        if title:
            clean_parts.append(title)
        if description:
            clean_parts.append(description)
        for record in (chat_records or []):
            sender = record.get("src_name", "")
            text = record.get("desc", "")
            if text:
                clean_parts.append(f"{sender}: {text}" if sender else text)
        clean_text = "\n".join(clean_parts)[:5000]

        return {
            "fav_id": int(fav_id),
            "type": int(item.get("type", 0)),
            "type_name": str(item.get("type_name", "")),
            "title": title[:500],
            "description": description[:1000],
            "link": link,
            "from_user": str(item.get("from_user", "")),
            "update_time": int(item.get("update_time", 0)),
            "chat_records_json": chat_records_json,
            "media_json": json.dumps(item.get("image_list", []), ensure_ascii=True),
            "clean_text": clean_text,
            "cached_at": int(time.time()),
        }

    @staticmethod
    def _get_fav_items_safe(reader, limit: int, offset: int) -> list[dict]:
        """安全获取收藏列表，batch 失败时降级为逐条加载。

        收藏数据中部分条目的 content 字段包含超大 XML（如嵌套聊天记录），
        直接批量查询 `SELECT *` 会触发 "result too large" 错误。
        此方法在首次失败后降级为：
        1. 只查元数据列（local_id, type, update_time, fromusr）
        2. 逐条调用 get_by_id(local_id) 加载内容

        Args:
            reader: WcdbFavReader 实例
            limit: 每页条数
            offset: 偏移量

        Returns:
            list[dict]: 收藏项列表
        """
        try:
            return reader.get_items(limit=limit, offset=offset) or []
        except Exception as e:
            logger.warning("[CACHE] fav batch 读失败 (%s)，降级为逐条加载", e)
        # Fallback: 元数据列 + 逐条内容
        try:
            safe_sql = (
                f"SELECT local_id, type, update_time, fromusr "
                f"FROM fav_db_item ORDER BY update_time DESC LIMIT {limit} OFFSET {offset}"
            )
            safe_rows = reader._exec(safe_sql) or []
        except Exception as e2:
            logger.warning("[CACHE] fav 元数据查询也失败: %s", e2)
            return []
        items = []
        for r in safe_rows:
            try:
                item = reader._parse_fav_row(r)
                lid = r["local_id"]
                # 逐条加载 content（与 api_handlers 降级方案完全一致）
                try:
                    c_sql = f"SELECT content FROM fav_db_item WHERE local_id={lid}"
                    c_rows = reader._exec(c_sql) or []
                    if c_rows and c_rows[0].get("content"):
                        content_str = c_rows[0]["content"]
                        item["content_raw"] = content_str
                        if content_str.strip().startswith("<favitem"):
                            item.update(reader._parse_fav_xml(content_str))
                except Exception:
                    logger.warning("[CACHE] fav 逐条加载失败: local_id=%s", lid)
                items.append(item)
            except Exception as e3:
                logger.warning("[CACHE] fav 行解析失败: %s", e3)
        return items

    def index_to_rag(self, rag_engine, source: str):
        """将缓存数据索引到 ChromaDB（线程安全 + pending 防丢）。"""
        if source not in self._reindexing:
            return

        # 抢占：已在跑 → 标记 pending 后立即返回
        with self._reindex_lock:
            if self._reindexing[source]:
                self._pending[source] = True
                logger.debug("[CACHE] RAG 索引 %s 已在进行中，标记 pending", source)
                return
            self._reindexing[source] = True

        try:
            # ── 第一轮：执行重索引 ──
            self._do_index(rag_engine, source)
            # ── 第二轮：检查 pending 期间是否有新数据 ──
            with self._reindex_lock:
                if self._pending[source]:
                    self._pending[source] = False
                    logger.info("[CACHE] RAG 索引 %s 完成后发现 pending 数据，再跑一轮", source)
                    # 递归再跑一次（但 _reindexing=True 会让任何新进入的调用走 pending 分支）
                    try:
                        self._do_index(rag_engine, source)
                    except Exception as e:
                        logger.warning("[CACHE] pending 重索引 %s 失败: %s", source, e)
        finally:
            with self._reindex_lock:
                self._reindexing[source] = False

    def _do_index(self, rag_engine, source: str):
        """执行一次具体的索引逻辑（不涉及锁）。

        不再无条件打"开始"日志：增量索引查询后若无新内容则完全静默
        （避免每分钟刷屏），有内容时由 _index_chunks 打一条带标题的汇总。
        """
        try:
            if source == "oa":
                self._index_oa(rag_engine)
            elif source == "sns":
                self._index_sns(rag_engine)
            elif source == "fav":
                self._index_fav(rag_engine)
        except Exception as e:
            logger.warning("[CACHE] RAG 索引 %s 失败: %s", source, e)

    def reset_index_cursor(self, source: str):
        """重置增量游标，下次 index_to_rag 会全量重索引。"""
        if source in self._last_indexed_at:
            self._last_indexed_at[source] = 0.0

    def _index_oa(self, rag):
        """索引 OA 文章到 ChromaDB（增量：只索引进 cached_at > last_indexed_at 的）。"""
        _cutoff = self._last_indexed_at["oa"]
        if _cutoff > 0:
            rows = self.query(
                "SELECT url, title, digest, full_content, source_name, pub_time, cached_at "
                "FROM oa_cache WHERE title != '' AND cached_at > ?",
                [_cutoff],
            )
        else:
            # 首次或重置：全量索引
            rows = self.query(
                "SELECT url, title, digest, full_content, source_name, pub_time, cached_at "
                "FROM oa_cache WHERE title != ''"
            )
        from src.assistant.rag_types import Chunk
        chunks = []
        max_cached_at = _cutoff
        for r in rows:
            text = f"{r['title']} {r['digest']}"
            if r['full_content']:
                text += f" {r['full_content'][:2000]}"
            chunks.append(Chunk(
                id=f"oa_{r['url']}",
                source="oa",
                source_id=r['url'],
                chat_id=r['source_name'],
                sender_name=r['source_name'],
                content=text[:3000],
                created_at=str(r['pub_time']),
            ))
            _ca = r['cached_at'] or 0
            if _ca > max_cached_at:
                max_cached_at = _ca
        self._index_chunks(rag, chunks, "OA 文章")
        # 更新游标
        if chunks and max_cached_at > self._last_indexed_at["oa"]:
            self._last_indexed_at["oa"] = max_cached_at
            self._save_index_cursor()

    def _index_sns(self, rag):
        """索引朋友圈到 ChromaDB（增量）。"""
        _cutoff = self._last_indexed_at["sns"]
        if _cutoff > 0:
            rows = self.query(
                "SELECT post_id, clean_content, nickname, create_time, cached_at "
                "FROM sns_cache WHERE clean_content != '' AND cached_at > ?",
                [_cutoff],
            )
        else:
            rows = self.query(
                "SELECT post_id, clean_content, nickname, create_time, cached_at "
                "FROM sns_cache WHERE clean_content != ''"
            )
        from src.assistant.rag_types import Chunk
        chunks = []
        max_cached_at = _cutoff
        for r in rows:
            text = f"{r['nickname']}: {r['clean_content']}"
            chunks.append(Chunk(
                id=f"sns_{r['post_id']}",
                source="sns",
                source_id=r['post_id'],
                chat_id=r['nickname'],
                sender_name=r['nickname'],
                content=text[:3000],
                created_at=str(r['create_time']),
            ))
            _ca = r['cached_at'] or 0
            if _ca > max_cached_at:
                max_cached_at = _ca
        self._index_chunks(rag, chunks, "朋友圈")
        if chunks and max_cached_at > self._last_indexed_at["sns"]:
            self._last_indexed_at["sns"] = max_cached_at
            self._save_index_cursor()

    def _index_fav(self, rag):
        """索引收藏到 ChromaDB（增量）。"""
        _cutoff = self._last_indexed_at["fav"]
        if _cutoff > 0:
            rows = self.query(
                "SELECT fav_id, clean_text, type_name, update_time, cached_at "
                "FROM fav_cache WHERE clean_text != '' AND cached_at > ?",
                [_cutoff],
            )
        else:
            rows = self.query(
                "SELECT fav_id, clean_text, type_name, update_time, cached_at "
                "FROM fav_cache WHERE clean_text != ''"
            )
        from src.assistant.rag_types import Chunk
        chunks = []
        max_cached_at = _cutoff
        for r in rows:
            chunks.append(Chunk(
                id=f"fav_{r['fav_id']}",
                source="fav",
                source_id=str(r['fav_id']),
                chat_id=r['type_name'],
                sender_name=r['type_name'],
                content=r['clean_text'][:3000],
                created_at=str(r['update_time']),
            ))
            _ca = r['cached_at'] or 0
            if _ca > max_cached_at:
                max_cached_at = _ca
        self._index_chunks(rag, chunks, "收藏")
        if chunks and max_cached_at > self._last_indexed_at["fav"]:
            self._last_indexed_at["fav"] = max_cached_at
            self._save_index_cursor()

    def _index_chunks(self, rag, chunks, label: str):
        """批量索引 chunks 到 ChromaDB。"""
        if not chunks:
            # 无新内容 → 静默（debug 级），避免每分钟刷屏
            logger.debug("[CACHE] %s: 无新内容可索引", label)
            return
        try:
            texts = [c.content for c in chunks]
            embeddings = rag._embedder.encode(texts)
            rag._store.add(chunks, embeddings)
            # 日志带标题摘要（前 5 条，每条截 25 字），便于确认索引了哪些内容
            _title_sum = " | ".join(
                "{}/{}".format((c.sender_name or "?")[:12], (c.content or "").replace("\n", " ")[:25])
                for c in chunks[:5]
            )
            logger.info("[CACHE] RAG 索引 %s: %d 条%s", label, len(chunks),
                        " | " + _title_sum if _title_sum else "")
        except Exception as e:
            logger.warning("[CACHE] RAG 索引 %s 失败: %s", label, e)


# ══════════════════════════════════════════════════════════════
# TaskCenter 辅助函数（每个 try/except 包裹）
# ══════════════════════════════════════════════════════════════

def _create_task(tc, task_type, group_id, group_name):
    if not tc:
        return None
    try:
        # 防重复：同类型 running 中不创建
        running = tc.list_tasks(status="running", task_type=task_type, limit=1)
        if running:
            return running[0]["id"]
        return tc.create_task(
            task_type=task_type, source="system",
            group_id=group_id or "", group_name=group_name or task_type,
        )
    except Exception as e:
        logger.warning("[CACHE] create_task 失败: %s", e)
        return None


def _update_task(tc, task_id, progress):
    if task_id and tc:
        try:
            tc.update_task(task_id, progress=progress)
        except Exception as e:
            # 纯 UI 进度辅助，高频（每轮缓存任务多次）用 debug 防刷屏。
            logger.debug("[CACHE] update_task 进度失败: %s", e)


def _complete_task(tc, task_id, result=""):
    if task_id and tc:
        try:
            tc.complete_task(task_id, result=result or "")
        except Exception as e:
            # 任务终态失败 → 任务卡 running（前端转圈）。低频（每任务一次）。
            logger.warning("[CACHE] complete_task 失败: %s", e)


def _fail_task(tc, task_id, error=""):
    if task_id and tc:
        try:
            tc.fail_task(task_id, error=error or "")
        except Exception as e:
            # 失败任务唯一落库出口，静默则错误信息丢失、任务卡 running。
            logger.warning("[CACHE] fail_task 失败: %s", e)
