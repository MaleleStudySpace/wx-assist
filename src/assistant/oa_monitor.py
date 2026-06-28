"""Official Account Monitor Engine — polls gh_xxx accounts for new articles and pushes alerts.

Unlike the keyword AlertEngine which hooks into the bot's message callback,
OAMonitorEngine runs its own background polling loop, because the bot currently
only polls @chatroom group sessions — gh_xxx OA sessions are never polled.

The engine reuses oa_parser.fetch_oa_articles() for reliable zstd decompression
and XML parsing, rather than re-implementing content decoding.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

from .config import AssistantConfig, OAMonitorGroup
from .outbox import Outbox

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
    1. Creates an outbox notification
    2. Optionally pushes to WeChat via iLink
    3. Marks the URL as alerted for dedup
    """

    def __init__(self, config: AssistantConfig, outbox: Outbox):
        self._config = config
        self._outbox = outbox
        # URL dedup: url -> timestamp
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
                time.sleep(1)

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
        """Check one OA account for new articles."""
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

        now = time.time()
        cutoff = now - MONITOR_MAX_AGE_SEC

        for art in articles:
            # Filter by age
            art_ts = art.timestamp or art.pub_time or 0
            if art_ts < cutoff:
                continue

            # Dedup by URL
            if not art.url or art.url in self._alerted_urls:
                continue

            # Mark as alerted immediately (before notification to prevent races)
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
            # OA article XML often has empty <digest> in WeChat 4.x,
            # Try AI-generated digest first, fall back to title
            digest = (art.digest or "")[:50] or title[:50]
            # AI摘要: 尝试调用 AI 生成摘要，失败则降级为标题
            try:
                from src.config import load_config
                from src.summarize import create_summarizer
                cfg = load_config()
                smrz = create_summarizer(cfg)
                ai_content = art.content or art.digest or title
                ai_digest = smrz.chat(
                    message=f"请用1-2句话总结以下文章的核心内容:
{ai_content[:500]}",
                    context_messages=[],
                    requester_name="system",
                    bot_name="摘要助手",
                    group_name=source,
                )
                if ai_digest and ai_digest.strip():
                    digest = ai_digest.strip()[:80]
                    logger.info(f"OAMonitor AI digest for '{title[:20]}': {digest[:30]}")
            except Exception as e:
                logger.warning(f"OAMonitor AI digest failed, falling back to title: {e}")

            notif_title = "🔔 公众号新文"
            notif_content = (
                f"公众号: {source}\n"
                f"时间: {time_str}\n"
                f"文章: 《{title}》\n"
                f"摘要: {digest}\n"
                f"链接: {art.url}"
            )

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

    def _push_to_wechat(self, nid: int, group_name: str, title: str, content: str) -> None:
        """Push notification to WeChat via iLink Bot."""
        try:
            from src.wechat.ilink_push import get_ilink_push, format_for_wechat
            ilink = get_ilink_push()
            if ilink.is_available():
                push_msg = format_for_wechat(title, content)
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

        cutoff = time.time() - DEDUP_MAX_AGE_SEC
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
