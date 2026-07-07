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
        # 全量同步进行中标记，防定时器重复触发
        self._syncing: dict[str, bool] = {
            "oa": False, "sns": False, "fav": False,
        }
        self._sync_lock = threading.Lock()
        self._init_tables()

    # ══════════════════════════════════════════════════════════════
    # 连接管理
    # ══════════════════════════════════════════════════════════════

    def _get_conn(self) -> sqlite3.Connection:
        """每次调用新连接，WAL 模式。"""
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ══════════════════════════════════════════════════════════════
    # DDL
    # ══════════════════════════════════════════════════════════════

    def _init_tables(self):
        """创建四张缓存表。启动时调用一次，失败则 Bot 初始化失败。"""
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
        CREATE INDEX IF NOT EXISTS idx_oa_gh_id ON oa_cache(gh_id);
        CREATE INDEX IF NOT EXISTS idx_oa_pub_time ON oa_cache(pub_time);

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
        CREATE INDEX IF NOT EXISTS idx_sns_username ON sns_cache(username);
        CREATE INDEX IF NOT EXISTS idx_sns_create_time ON sns_cache(create_time);

        CREATE TABLE IF NOT EXISTS fav_cache (
            fav_id           INTEGER PRIMARY KEY,
            type             INTEGER DEFAULT 0,
            type_name        TEXT DEFAULT '',
            title            TEXT DEFAULT '',
            description      TEXT DEFAULT '',
            link             TEXT DEFAULT '',
            from_user        TEXT DEFAULT '',
            update_time      INTEGER DEFAULT 0,
            chat_records_json TEXT DEFAULT '',  -- 完整解析的嵌套聊天记录
            media_json       TEXT DEFAULT '',
            clean_text       TEXT DEFAULT '',   -- 展平所有文字（含嵌套），供 RAG 用
            cached_at        INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_fav_type ON fav_cache(type);
        CREATE INDEX IF NOT EXISTS idx_fav_update_time ON fav_cache(update_time);
        """
        conn = self._get_conn()
        try:
            conn.executescript(ddl)
            conn.commit()
            logger.info("[CACHE] 四张缓存表已就绪")
        except Exception as e:
            logger.warning("[CACHE] 建表失败: %s", e)
            raise
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
        """同步 OA 账号列表。"""
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
        now = int(time.time())
        for s in sessions:
            uid = s.get("username", "")
            if not uid:
                continue
            accounts.append({
                "gh_id": uid,
                "display_name": names.get(uid, uid) or uid,
                "avatar_url": "",
                "last_updated": now,
            })
        if accounts:
            self.batch_upsert("oa_accounts", accounts)
            logger.info("[CACHE] OA 账号同步: %d 个", len(accounts))

    def _sync_oa_articles_full(self, client, task_id=None, task_center=None):
        """全量同步 OA 文章：遍历每个 gh_id，拉最新 50 篇。"""
        accounts = self.query("SELECT gh_id FROM oa_accounts")
        if not accounts:
            logger.info("[CACHE] OA 文章全量跳过：无 OA 账号")
            return
        total_new = 0
        for i, row in enumerate(accounts):
            gh_id = row["gh_id"]
            try:
                new = self._sync_oa_gh(client, gh_id)
                total_new += new
            except Exception as e:
                logger.warning("[CACHE] OA 文章同步 %s 失败: %s", gh_id, e)
            if task_id and (i + 1) % 5 == 0:
                _update_task(task_center, task_id,
                             f"第 {i+1}/{len(accounts)} 个公众号")
        if total_new:
            logger.info("[CACHE] OA 全量同步完成: %d 个公众号, %d 篇文章",
                        len(accounts), total_new)

    def _sync_oa_gh(self, client, gh_id: str) -> int:
        """同步单个 OA 公众号的文章。返回新增条数。"""
        from src.assistant.oa_parser import fetch_oa_articles, OAArticle
        articles = fetch_oa_articles(client, gh_id, limit=50)
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

    def sync_oa_incremental(self, client, task_center=None):
        """增量合并 OA 文章。定时器 60s + 用户访问触发。"""
        if self._is_full_syncing("oa"):
            logger.debug("[CACHE] OA 全量进行中，增量跳过")
            return
        if client is None:
            logger.warning("[CACHE] WCDB 不可用, OA 同步跳过")
            return
        accounts = self.query("SELECT gh_id FROM oa_accounts")
        if not accounts:
            return
        tid = _create_task(task_center, "cache_oa_incremental", "", "OA增量同步")
        total = 0
        try:
            existing = self._get_existing_oa_urls()
            for row in accounts:
                gh_id = row["gh_id"]
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
                except Exception as e:
                    logger.warning("[CACHE] OA 增量 %s 失败: %s", gh_id, e)
            if total:
                logger.info("[CACHE] OA 增量合并: 新增 %d 篇文章", total)
            _complete_task(task_center, tid, f"OA 增量: 新增 {total} 篇")
        except Exception as e:
            logger.warning("[CACHE] OA 增量合并失败: %s", e)
            _fail_task(task_center, tid, str(e))

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
        row = self.query_one(
            "SELECT url FROM oa_cache WHERE content_status=0 LIMIT 1"
        )
        if not row:
            # 没有待抓取文章时，重置任务状态
            if self._fetcher_task_id:
                _complete_task(self._fetcher_tc, self._fetcher_task_id,
                               f"抓取完成: {self._fetcher_count} 篇")
                self._fetcher_task_id = None
                self._fetcher_count = 0
            return

        # 首次有文章时创建任务
        if not self._fetcher_task_id and self._fetcher_tc:
            self._fetcher_task_id = _create_task(
                self._fetcher_tc, "cache_oa_content", "", "OA全文抓取"
            )

        url = row["url"]
        try:
            from src.assistant.oa_reader import fetch_article_content
            content = fetch_article_content(url, timeout=15)
            if content:
                import html, re
                content = html.unescape(content)
                content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', content)
                self.update("oa_cache", {
                    "full_content": content[:50000],  # 最长 5 万字
                    "content_status": 1,
                }, {"url": url})
                self._fetcher_count += 1
                # 每 5 篇更新一次任务进度
                if self._fetcher_count % 5 == 0 and self._fetcher_task_id:
                    _update_task(self._fetcher_tc, self._fetcher_task_id,
                                 f"已抓取 {self._fetcher_count} 篇")
            else:
                self.update("oa_cache", {"content_status": -1}, {"url": url})
        except Exception as e:
            # 403/429 标记失败，其他保持 0 下次重试
            resp_err = getattr(e, "response", None)
            status = getattr(resp_err, "status_code", 0) if resp_err else 0
            if status in (403, 429):
                logger.warning("[CACHE] OA 全文抓取失败 %s (HTTP %d)", url, status)
                self.update("oa_cache", {"content_status": -1}, {"url": url})
            else:
                logger.debug("[CACHE] OA 全文抓取重试 %s: %s", url, e)

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

    def sync_sns_incremental(self, client, task_center=None):
        """增量合并朋友圈：只拉第 1 页。"""
        if self._is_full_syncing("sns"):
            return
        if client is None:
            logger.warning("[CACHE] WCDB 不可用, SNS 增量同步跳过")
            return
        tid = _create_task(task_center, "cache_sns_incremental", "", "朋友圈增量")
        try:
            posts = client.get_sns_timeline(limit=20, offset=0)
            if not posts:
                _complete_task(task_center, tid, "朋友圈增量: 0 条")
                return
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
            _complete_task(task_center, tid, f"朋友圈增量: {len(new)} 条")
        except Exception as e:
            logger.warning("[CACHE] 朋友圈增量合并失败: %s", e)
            _fail_task(task_center, tid, str(e))

    def _get_existing_sns_ids(self) -> set:
        rows = self.query("SELECT post_id FROM sns_cache")
        return {r["post_id"] for r in rows}

    @staticmethod
    def _clean_sns(raw: dict) -> dict | None:
        """清洗朋友圈。hex 解码 content，保留完整媒体结构。"""
        post_id = raw.get("tid") or raw.get("id")
        if not post_id:
            return None
        content_hex = raw.get("content") or raw.get("messageContent") or ""
        clean_content = ""
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
        try:
            from src.wechat.wcdb_fav_reader import WcdbFavReader
            reader = WcdbFavReader(client)
            total = 0
            max_pages = 5  # 5 页 × 200 条 = 1000
            existing = self._get_existing_fav_ids()
            for page in range(max_pages):
                items = reader.get_items(limit=200, offset=page * 200)
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
                if tid and (page + 1) % 2 == 0:
                    _update_task(task_center, tid,
                                 f"第 {page+1}/{max_pages} 页，已同步 {total} 条")
            logger.info("[CACHE] 收藏全量同步完成: %d 条", total)
            _complete_task(task_center, tid, f"收藏全量: {total} 条")
        except Exception as e:
            logger.warning("[CACHE] 收藏全量同步失败: %s", e)
            _fail_task(task_center, tid, str(e))
        finally:
            self._end_full_sync("fav")

    def sync_fav_incremental(self, client, task_center=None):
        """增量合并收藏。"""
        if self._is_full_syncing("fav"):
            return
        tid = _create_task(task_center, "cache_fav_incremental", "", "收藏增量")
        try:
            from src.wechat.wcdb_fav_reader import WcdbFavReader
            reader = WcdbFavReader(client)
            items = reader.get_items(limit=200, offset=0)
            if not items:
                _complete_task(task_center, tid, "收藏增量: 0 条")
                return
            max_cached = self._get_max_fav_id()
            new = []
            for item in items:
                fid = item.get("local_id")
                if fid and fid > max_cached:
                    cleaned = self._clean_fav(item)
                    if cleaned:
                        new.append(cleaned)
            if new:
                self.batch_upsert("fav_cache", new)
            logger.info("[CACHE] 收藏增量合并: 新增 %d 条", len(new))
            _complete_task(task_center, tid, f"收藏增量: {len(new)} 条")
        except Exception as e:
            logger.warning("[CACHE] 收藏增量合并失败: %s", e)
            _fail_task(task_center, tid, str(e))

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

    # ══════════════════════════════════════════════════════════════
    # RAG 索引
    # ══════════════════════════════════════════════════════════════

    def index_to_rag(self, rag_engine, source: str):
        """将缓存数据索引到 ChromaDB。"""
        logger.info("[CACHE] RAG 索引开始: source=%s", source)
        try:
            if source == "oa":
                self._index_oa(rag_engine)
            elif source == "sns":
                self._index_sns(rag_engine)
            elif source == "fav":
                self._index_fav(rag_engine)
        except Exception as e:
            logger.warning("[CACHE] RAG 索引 %s 失败: %s", source, e)

    def _index_oa(self, rag):
        """索引 OA 文章到 ChromaDB。"""
        rows = self.query(
            "SELECT url, title, digest, full_content, source_name, pub_time "
            "FROM oa_cache WHERE title != ''"
        )
        from src.assistant.rag.models import Chunk
        import numpy as np
        chunks = []
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
        self._index_chunks(rag, chunks, "OA 文章")

    def _index_sns(self, rag):
        """索引朋友圈到 ChromaDB。"""
        rows = self.query(
            "SELECT post_id, clean_content, nickname, create_time "
            "FROM sns_cache WHERE clean_content != ''"
        )
        from src.assistant.rag.models import Chunk
        chunks = []
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
        self._index_chunks(rag, chunks, "朋友圈")

    def _index_fav(self, rag):
        """索引收藏到 ChromaDB。"""
        rows = self.query(
            "SELECT fav_id, clean_text, type_name, update_time "
            "FROM fav_cache WHERE clean_text != ''"
        )
        from src.assistant.rag.models import Chunk
        chunks = []
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
        self._index_chunks(rag, chunks, "收藏")

    def _index_chunks(self, rag, chunks, label: str):
        """批量索引 chunks 到 ChromaDB。"""
        if not chunks:
            logger.info("[CACHE] %s: 无新内容可索引", label)
            return
        try:
            texts = [c.content for c in chunks]
            embeddings = rag._embedder.encode(texts)
            rag._store.add(chunks, embeddings)
            logger.info("[CACHE] RAG 索引 %s: %d 条", label, len(chunks))
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
        except Exception:
            pass


def _complete_task(tc, task_id, result=""):
    if task_id and tc:
        try:
            tc.complete_task(task_id, result=result or "")
        except Exception:
            pass


def _fail_task(tc, task_id, error=""):
    if task_id and tc:
        try:
            tc.fail_task(task_id, error=error or "")
        except Exception:
            pass
