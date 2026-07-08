"""Official Account Monitor Engine — polls gh_xxx accounts for new articles and pushes alerts.

Unlike the keyword AlertEngine which hooks into the bot's message callback,
OAMonitorEngine runs its own background polling loop, because the bot currently
only polls @chatroom group sessions — gh_xxx OA sessions are never polled.

The engine reuses oa_parser.fetch_oa_articles() for reliable zstd decompression
and XML parsing, rather than re-implementing content decoding.
"""

import logging
import threading
import time as _time
from datetime import datetime
from typing import Optional

from .config import AssistantConfig, OAMonitorGroup
from .outbox import Outbox
from src.utils.llm_logger import log_llm_interaction

logger = logging.getLogger(__name__)

# Poll every 60 seconds — OA articles are rare enough that this is responsive
# without hammering WCDB.
POLL_SEC = 60

# Only alert on articles newer than this (seconds) — prevents startup spam
# and re-alerting on already-seen articles during the first poll.
MONITOR_MAX_AGE_SEC = 300  # 5 minutes

# Max number of alerted URLs to keep in memory for dedup.
DEDUP_MAX = 5000

# Cleanup alerted URLs older than 7 days.
DEDUP_MAX_AGE_SEC = 7 * 86400


class OAMonitorEngine:
    """Monitor OA accounts for new articles and push instant alerts.

    Runs a background daemon thread that polls WCDB for new articles from
    monitored gh_xxx accounts. When a new article is found, it:
    1. Writes to oa_cache (ContentCache) for persistence
    2. Creates an outbox notification
    3. Optionally pushes to WeChat via iLink

    If content_cache is provided, URL dedup uses the persistent cache
    instead of the in-memory set (enables cross-restart dedup).
    """

    def __init__(self, config: AssistantConfig, outbox: Outbox,
                 content_cache=None):
        self._config = config
        self._outbox = outbox
        self._content_cache = content_cache
        # URL dedup: url -> timestamp (secondary, per-session dedup only)
        self._alerted_urls: dict[str, float] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="oa-monitor")
        self._thread.start()
        logger.info("OAMonitorEngine started, monitoring %d groups", len(self._config.oa_monitor_groups))

    def stop(self) -> None:
        """Stop the background polling thread."""
        self._running = False
        logger.info("OAMonitorEngine stopping")

    def update_config(self, config: AssistantConfig) -> None:
        """Hot-reload configuration (called when user saves OA monitor settings)."""
        self._config = config
        logger.info("OAMonitorEngine config updated, now monitoring %d groups", len(config.oa_monitor_groups))

    # ── Polling loop ────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Main polling loop — runs in daemon thread."""
        while self._running:
            try:
                self._poll_cycle()
            except Exception as e:
                logger.error("OAMonitor poll cycle error: %s", e, exc_info=True)

            # Sleep in short intervals so stop() is responsive
            for _ in range(POLL_SEC):
                if not self._running:
                    return
                _time.sleep(1)

    def _poll_cycle(self) -> None:
        """One poll cycle: check all enabled monitor groups for new articles."""
        if not self._config.assistant_enabled:
            return

        for mg in self._config.oa_monitor_groups:
            if not mg.enabled or not mg.accounts:
                continue

            for gh_id in mg.accounts:
                try:
                    self._check_account(mg, gh_id)
                except Exception as e:
                    logger.warning("OAMonitor: error checking %s: %s", gh_id, e)

        # Periodic dedup cleanup
        self._cleanup_dedup()

    def _check_account(self, mg: OAMonitorGroup, gh_id: str) -> None:
        """Check one OA account for new articles.

        Writes all fetched articles to oa_cache (ContentCache) for persistence,
        then sends notifications for recent, previously unseen articles.
        """
        from .oa_parser import fetch_oa_articles

        # Lazily get WCDB client — it's a singleton in api_handlers,
        # not passed from bot.py (which doesn't store it as an attribute).
        client = self._get_wcdb_client()
        if not client:
            return

        try:
            articles = fetch_oa_articles(client, gh_id, limit=10)
        except Exception as e:
            logger.warning("OAMonitor: fetch_oa_articles(%s) failed: %s", gh_id, e)
            return

        if not articles:
            return

        now = _time.time()
        cutoff = now - MONITOR_MAX_AGE_SEC
        cached_new = 0

        for art in articles:
            art_ts = art.timestamp or art.pub_time or 0
            in_window = art_ts >= cutoff

            if not art.url:
                continue

            # ── Dedup: check cache + per-session set ─────────────────
            is_known = art.url in self._alerted_urls
            if not is_known and self._content_cache:
                try:
                    existing = self._content_cache.query_one(
                        "SELECT 1 FROM oa_cache WHERE url=?", [art.url]
                    )
                    is_known = existing is not None
                except Exception:
                    pass

            # ── Persist to oa_cache (INSERT OR REPLACE, idempotent) ──
            if not is_known:
                self._cache_article(art)
                cached_new += 1

            # ── Skip notification for out-of-window or already known ──
            if not in_window or is_known:
                continue

            # Mark as alerted immediately (prevents race within same poll)
            self._alerted_urls[art.url] = now

            # Format time
            time_str = ""
            if art_ts:
                try:
                    time_str = datetime.fromtimestamp(art_ts).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    time_str = ""

            # Build notification content
            source = art.source_name or gh_id
            title = art.title or "(无标题)"

            # ── AI 摘要：后台线程 + 5s 超时，防止慢 LLM 阻塞 OA Monitor 轮询 ──
            digest = ""
            llm_summary_text = ""
            llm_ok = 0
            try:
                from src.config import load_config
                from src.summarize import create_summarizer
                cfg = load_config()
                smrz = create_summarizer(cfg)
                input_text = (art.digest or art.title or "无摘要")[:500]
                prompt = "请用1-2句话总结以下文章的核心内容:\n" + input_text
                _t0 = _time.monotonic()

                # 后台线程执行 LLM 调用，硬超时 5 秒
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _exe:
                    _fut = _exe.submit(
                        smrz.chat,
                        message=prompt,
                        context_messages=[],
                        requester_name="system",
                        group_name=source,
                    )
                    try:
                        ai_digest = _fut.result(timeout=5)
                    except concurrent.futures.TimeoutError:
                        logger.warning("OAMonitor: LLM 超时（5s），使用原标题: %s", title[:20])
                        ai_digest = ""
                    except Exception as e:
                        logger.debug("OAMonitor: LLM 调用失败: %s", e)
                        ai_digest = ""

                _latency = (_time.monotonic() - _t0) * 1000
                _resp = (ai_digest or "").strip()
                log_llm_interaction(
                    backend="oa_monitor", call_type="oa_article_summary",
                    model=getattr(smrz, 'model', 'unknown'),
                    system_prompt="", user_prompt=prompt,
                    response=_resp[:200], latency_ms=_latency,
                    extra={"title": title[:50], "url": art.url, "gh_id": gh_id, "timed_out": _resp=="" and _latency>=4900},
                )
                if _resp:
                    digest = _resp[:80]
                    llm_summary_text = _resp[:500]
                    llm_ok = 1
                    logger.info("OAMonitor AI digest for '%s': %s", title[:20], digest[:40])
                else:
                    logger.info("OAMonitor AI digest returned empty for '%s', use title", title[:20])
            except Exception as e:
                logger.warning("OAMonitor AI digest failed for '%s': %s", title[:20], e)
                # log failure (latency = 0 since we don't have it on exception path here)
                log_llm_interaction(
                    backend="oa_monitor", call_type="oa_article_summary",
                    model="unknown", system_prompt="",
                    user_prompt=(art.digest or art.title or "无摘要")[:200],
                    response=f"[Error: {e}]", latency_ms=0,
                    extra={"title": title[:50], "url": art.url, "error": str(e)[:200]},
                )

            # 最终展示用的 digest：AI摘要 > 原文摘要 > 标题
            if not digest:
                digest = (art.digest or "")[:50] or title[:50]

            # ── 保存 LLM 摘要到 oa_cache ──
            if llm_ok and llm_summary_text and self._content_cache:
                try:
                    self._content_cache.update("oa_cache", {
                        "llm_summary": llm_summary_text,
                        "llm_summary_ok": 1,
                        "cached_at": int(_time.time()),
                    }, {"url": art.url})
                except Exception:
                    pass

            import json as _json

            notif_title = f"🔔 新文章 · {source}"
            notif_content = _json.dumps({
                "group": source,
                "time": time_str,
                "article_title": title,
                "digest": digest,
                "url": art.url,
                "display": (
                    f"📰 **文章:** {title}\n"
                    f"🕐 **时间:** {time_str}\n"
                    f"\n{digest}\n"
                    f"\n🔗 **原文链接:** {art.url}"
                ),
            }, ensure_ascii=False)

            # Write to outbox
            nid = self._outbox.add(
                notif_type="oa_article_alert",
                chat_id=gh_id,
                group_name=mg.name or source,
                title=notif_title,
                content=notif_content,
                priority="high",
            )
            logger.info(
                "OAMonitor: new article '%s' from %s (group=%s)",
                title[:30], source, mg.name,
            )

            # Push to WeChat via iLink (if configured)
            if mg.push_target == "ilink":
                self._push_to_wechat(nid, mg.name or source, notif_title, notif_content)

        # ── 本轮有新文章写入 oa_cache → 触发 RAG 重索引 ──
        if cached_new > 0:
            try:
                from src.web.server import get_rag_engine
                _re = get_rag_engine()
                if _re and self._content_cache:
                    self._content_cache.index_to_rag(_re, "oa")
            except Exception:
                pass

    def _cache_article(self, art) -> None:
        """Write a single OA article to oa_cache. Idempotent (INSERT OR REPLACE).

        Delegates to ContentCache._clean_oa() to avoid code duplication.
        """
        if not self._content_cache:
            return
        try:
            cleaned = self._content_cache._clean_oa(art)
            if cleaned:
                self._content_cache.upsert("oa_cache", cleaned)
        except Exception as e:
            logger.warning("[CACHE] _cache_article 失败: %s", e)

    def _push_to_wechat(self, nid: int, group_name: str, title: str, content: str) -> None:
        """Push notification to WeChat via iLink Bot."""
        try:
            from src.wechat.ilink_push import get_ilink_push, format_for_wechat
            import json as _json
            ilink = get_ilink_push()
            if ilink.is_available():
                # Extract display text from JSON content (not raw JSON)
                push_data = _json.loads(content) if isinstance(content, str) else content
                push_text = push_data.get("display", content)
                push_msg = format_for_wechat(title, push_text)
                result = ilink.send_message(push_msg)
                push_ok = result.get("success", False)
                push_err = result.get("error", "") if not push_ok else ""
                self._outbox.update_push_result(
                    nid, "ilink",
                    "success" if push_ok else "failed",
                    push_err,
                )
                if push_ok:
                    logger.info("OAMonitor: pushed to WeChat for '%s'", group_name)
                else:
                    logger.warning("OAMonitor: WeChat push failed for '%s': %s", group_name, push_err)
                try:
                    from src.web.api_handlers import broadcast_event
                    broadcast_event("oa_monitor_push_result", {
                        "group_name": group_name,
                        "success": push_ok,
                        "error": push_err,
                    })
                except Exception:
                    pass
            else:
                logger.warning("OAMonitor: WeChat push skipped for '%s': iLink not bound", group_name)
        except Exception as e:
            logger.warning("OAMonitor: WeChat push error for '%s': %s", group_name, e)
            try:
                self._outbox.update_push_result(nid, "ilink", "failed", str(e))
            except Exception:
                pass

    # ── Dedup management ────────────────────────────────────────────────

    # ── WCDB client access ────────────────────────────────────────────

    def _get_wcdb_client(self):
        """Get the WCDB client singleton (lazily, from api_handlers).

        The WCDB client is owned by api_handlers, not bot.py.
        Using getattr(bot, '_wcdb_client') always returns None.
        """
        try:
            from src.web.api_handlers import get_wcdb_client
            return get_wcdb_client()
        except Exception as e:
            logger.warning("OAMonitor: cannot get WCDB client: %s", e)
            return None

    def _cleanup_dedup(self) -> None:
        """Remove old entries from alerted URLs to prevent unbounded growth."""
        if len(self._alerted_urls) <= DEDUP_MAX:
            return

        cutoff = _time.time() - DEDUP_MAX_AGE_SEC
        stale = [url for url, ts in self._alerted_urls.items() if ts < cutoff]
        for url in stale:
            del self._alerted_urls[url]

        if stale:
            logger.info("OAMonitor dedup cleanup: removed %d entries (>%d days)", len(stale), DEDUP_MAX_AGE_SEC // 86400)

        # Hard cap: if still too many after age cleanup, trim oldest
        if len(self._alerted_urls) > DEDUP_MAX:
            sorted_items = sorted(self._alerted_urls.items(), key=lambda x: x[1])
            to_remove = len(self._alerted_urls) - DEDUP_MAX
            for url, _ in sorted_items[:to_remove]:
                del self._alerted_urls[url]
