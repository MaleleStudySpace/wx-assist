"""
Official Account Digest Service — generates AI summaries for OA articles

Supports:
- Per-group scheduled digest generation
- Differentiated LLM prompts per group
- Article deduplication by URL
- Push to configured target (chatroom/user)
- Full article content scraping + summarization
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from src.assistant.oa_parser import (
    OAArticle,
    decode_content,
    fetch_oa_articles,
    get_oa_sessions,
    parse_oa_article,
)
from src.assistant.oa_reader import fetch_article_content
from src.utils.llm_logger import log_llm_interaction

logger = logging.getLogger(__name__)

# ── Digest Prompt Templates ───────────────────────────────────────────

DIGEST_TEMPLATES = {
    "default": (
        "你是一个专业的公众号信息摘要助手。请严格按照以下格式输出摘要：\n\n"
        "对每篇文章，按此格式输出一行：\n"
        "公众号{公众号名}： {yyyy-mm-dd hh:mm} 文章标题为：《{标题}》 摘要：{核心要点，≤50字}\n\n"
        "所有文章输出完毕后：\n"
        "1. 列出原文链接\n"
        "2. 写一段2-3句的总结，概括这批文章的核心主题和价值\n\n"
        "要求：\n"
        "1. 每篇文章一行，格式统一\n"
        "2. 摘要提炼核心要点，不重复标题\n"
        "3. 总结要有洞察，不要简单罗列"
    ),
    "tech": (
        "你是一个科技类文章摘要专家。请严格按照以下格式输出摘要：\n\n"
        "对每篇文章，按此格式输出一行：\n"
        "公众号{公众号名}： {yyyy-mm-dd hh:mm} 文章标题为：《{标题}》 技术摘要：{技术要点、架构方案、创新点，≤80字}\n\n"
        "所有文章输出完毕后：\n"
        "1. 列出原文链接\n"
        "2. 写一段2-3句的技术总结，评估技术价值和行业影响\n\n"
        "要求：\n"
        "1. 每篇文章一行，格式统一\n"
        "2. 使用专业术语，保持技术准确性\n"
        "3. 关注代码、架构、性能、AI等技术维度"
    ),
    "entertainment": (
        "你是娱乐新闻摘要员。请严格按照以下格式输出摘要：\n\n"
        "对每篇文章，按此格式输出一行：\n"
        "公众号{公众号名}： {yyyy-mm-dd hh:mm} 文章标题为：《{标题}》 一句话：{核心事件+关键人物，≤30字}\n\n"
        "所有文章输出完毕后：\n"
        "1. 列出原文链接\n"
        "2. 一句话总结今日娱乐圈最值得关注的事\n\n"
        "要求：简洁有趣，适合快速浏览"
    ),
    "business": (
        "你是商业分析摘要专家。请严格按照以下格式输出摘要：\n\n"
        "对每篇文章，按此格式输出一行：\n"
        "公众号{公众号名}： {yyyy-mm-dd hh:mm} 文章标题为：《{标题}》 商业摘要：{核心数据/趋势/投资信号，≤60字}\n\n"
        "所有文章输出完毕后：\n"
        "1. 列出原文链接\n"
        "2. 写一段2-3句的商业总结，提炼市场趋势和投资启示\n\n"
        "要求：\n"
        "1. 聚焦数据、趋势、投资信号\n"
        "2. 忽略情绪化内容，保持客观"
    ),
    "news": (
        "你是新闻摘要专家。请严格按照以下格式输出摘要：\n\n"
        "对每篇文章，按此格式输出一行：\n"
        "公众号{公众号名}： {yyyy-mm-dd hh:mm} 文章标题为：《{标题}》 新闻摘要：{谁+什么事+关键数据，≤50字}\n\n"
        "所有文章输出完毕后：\n"
        "1. 列出原文链接\n"
        "2. 写一段2-3句的总结，补充背景和影响\n\n"
        "要求：\n"
        "1. 遵循5W1H（谁/什么/何时/何地/为什么/如何）\n"
        "2. 保留关键数据和引言"
    ),
}


# ── Digest History (dedup) ────────────────────────────────────────────

class DigestHistory:
    """Track digested article URLs to avoid duplicates, with timestamp-based cleanup."""

    def __init__(self, data_dir: str = "data"):
        self._path = Path(data_dir) / "oa_digest_history.json"
        self._urls: dict[str, float] = {}  # url -> timestamp
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                raw_urls = data.get("urls", [])
                # Support legacy format: urls as a list (set) -> convert to dict with ts=0
                if isinstance(raw_urls, list):
                    self._urls = {url: 0.0 for url in raw_urls}
                elif isinstance(raw_urls, dict):
                    self._urls = {url: float(ts) for url, ts in raw_urls.items()}
                else:
                    self._urls = {}
            except Exception:
                self._urls = {}

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"urls": self._urls}  # dict format: url -> timestamp
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_digested(self, url: str) -> bool:
        return url in self._urls

    def mark_digested(self, url: str):
        self._urls[url] = time.time()
        self._save()

    def get_undigested(self, articles: list[OAArticle]) -> list[OAArticle]:
        """Filter out already-digested articles."""
        return [a for a in articles if not self.is_digested(a.url)]

    def cleanup(self, max_age_days: int = 30):
        """Remove entries older than max_age_days to prevent unbounded growth."""
        cutoff = time.time() - max_age_days * 86400
        stale = [url for url, ts in self._urls.items() if ts < cutoff and ts > 0]
        for url in stale:
            del self._urls[url]
        if stale:
            logger.info("DigestHistory cleanup: removed %d entries older than %d days", len(stale), max_age_days)
            self._save()


# ── LLM Interface ─────────────────────────────────────────────────────

def call_llm(prompt: str, system_prompt: str = "", summarizer=None) -> str:
    """Call LLM API — uses summarizer._call_long_api if available, else raw requests."""
    if summarizer is not None:
        try:
            start = time.monotonic()
            content = summarizer._call_long_api(
                system_prompt,
                [{"role": "user", "content": prompt}],
                max_tokens=2000,
                temperature=0.3,
            )
            latency = (time.monotonic() - start) * 1000
            log_llm_interaction(
                backend="oa_digest", call_type="oa_digest",
                model=getattr(summarizer, 'model', 'unknown'),
                system_prompt=system_prompt,
                user_prompt=prompt, response=content,
                latency_ms=latency,
                extra={"temperature": 0.3, "max_tokens": 2000},
            )
            logger.info("[OA-DIGEST] LLM response: %d chars, preview=%s", len(content), content[:100])
            return content
        except Exception as e:
            logger.error("LLM call via summarizer failed: %s, falling back to raw requests", e)
            # Fall through to raw requests fallback

    # Legacy fallback: raw requests.post (for when no summarizer is available)
    # Read AI config from bot config (not _load_env which doesn't exist)
    try:
        from src.config import load_config
        bot_cfg = load_config()
        api_key = bot_cfg.ai_provider_api_key
        api_base = bot_cfg.ai_provider_base_url or "https://api.deepseek.com"
        model = bot_cfg.ai_provider_model or "DeepSeek-V4-Pro"
    except Exception as e:
        logger.error("[OA-DIGEST] Failed to load bot config for LLM call: %s", e)
        return "[Error: AI配置无法加载，请检查 .env 文件]"

    if not api_key:
        logger.error("[OA-DIGEST] AI_PROVIDER_API_KEY not configured — cannot call LLM")
        return "[Error: AI未配置，请先在设置中配置AI提供商（API Key）]"

    import requests

    # Ensure /v1 path
    if not api_base.endswith("/v1"):
        api_base = api_base.rstrip("/") + "/v1"

    url = f"{api_base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
    }

    try:
        start = time.monotonic()
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        latency = (time.monotonic() - start) * 1000
        log_llm_interaction(
            backend="oa_digest", call_type="oa_digest",
            model=model, system_prompt=system_prompt,
            user_prompt=prompt, response=content,
            latency_ms=latency,
            extra={"api_base": api_base, "temperature": 0.3, "max_tokens": 2000},
        )
        logger.info("[OA-DIGEST] LLM response (fallback): %d chars, preview=%s", len(content), content[:100])
        return content
    except Exception as e:
        latency_ms = (time.monotonic() - start) * 1000 if 'start' in dir() else 0
        log_llm_interaction(
            backend="oa_digest", call_type="oa_digest",
            model=model, system_prompt=system_prompt,
            user_prompt=prompt, response=f"[Error: {e}]",
            latency_ms=latency_ms,
            extra={"api_base": api_base, "error": str(e)},
        )
        logger.error("[OA-DIGEST] LLM call failed: %s", e)
        return f"[Error: {e}]"


# ── Digest Service ────────────────────────────────────────────────────

class OADigestService:
    """Service for generating OA article digests."""

    def __init__(self, config, wcdb_client=None, summarizer=None):
        """
        Args:
            config: AssistantConfig instance
            wcdb_client: WcdbNativeClient instance
            summarizer: AbstractSummarizer instance (for unified LLM calls)
        """
        self._config = config
        self._client = wcdb_client
        self._summarizer = summarizer
        self._history = DigestHistory()

    def generate_digest(
        self,
        group_id: str,
        scrape_full: bool = True,
        max_content_chars: int = 8000,
    ) -> dict:
        """Generate a digest for an OA group.

        Args:
            group_id: The group to generate digest for
            scrape_full: Whether to scrape full article content
            max_content_chars: Max chars per article for LLM input

        Returns:
            dict with: success, group_id, articles_count, digest_text, errors
        """
        from src.assistant.oa_groups import OAGroupManager

        # Cleanup stale history entries on each digest run
        self._history.cleanup()

        manager = OAGroupManager(self._config)
        group = manager.get_group(group_id)
        if not group:
            return {"success": False, "error": f"Group {group_id} not found"}

        if not self._client:
            return {"success": False, "error": "WCDB client not available"}

        # Fetch articles from all accounts in group
        all_articles = []
        for gh_id in group.accounts:
            try:
                articles = fetch_oa_articles(self._client, gh_id, limit=50)
                all_articles.extend(articles)
                logger.info(
                    "[OA-DIGEST] Fetched %d articles from %s for group '%s'",
                    len(articles), gh_id, group.name,
                )
            except Exception as e:
                logger.error("[OA-DIGEST] Failed to fetch articles from %s: %s", gh_id, e)

        logger.info(
            "[OA-DIGEST] Generating digest for group '%s' (%d accounts, %d articles fetched)",
            group.name, len(group.accounts), len(all_articles),
        )

        if not all_articles:
            return {
                "success": True,
                "group_id": group_id,
                "articles_count": 0,
                "digest_text": "没有新的公众号文章",
                "errors": [],
            }

        # Apply lookback time filter
        lookback_hours = self._calc_effective_lookback(group)
        if lookback_hours > 0:
            cutoff = time.time() - lookback_hours * 3600
            before_count = len(all_articles)
            all_articles = [
                a for a in all_articles
                if (a.timestamp or a.pub_time or 0) >= cutoff
            ]
            logger.info(
                "Lookback filter: %dh cutoff=%d, %d→%d articles",
                lookback_hours, int(cutoff), before_count, len(all_articles),
            )

        if not all_articles:
            return {
                "success": True,
                "group_id": group_id,
                "articles_count": 0,
                "digest_text": f"最近 {lookback_hours} 小时内没有新的公众号文章",
                "errors": [],
            }

        # Deduplicate
        before_dedup = len(all_articles)
        new_articles = self._history.get_undigested(all_articles)
        logger.info(
            "[OA-DIGEST] Dedup: %d total → %d new articles (history has %d entries)",
            before_dedup, len(new_articles), len(self._history._urls),
        )
        if not new_articles:
            return {
                "success": True,
                "group_id": group_id,
                "articles_count": 0,
                "digest_text": "所有文章已摘要过，无新内容",
                "errors": [],
            }

        # Get the digest template — custom_prompt takes priority
        if group.custom_prompt:
            system_prompt = group.custom_prompt
        else:
            template_key = group.digest_template or "default"
            system_prompt = DIGEST_TEMPLATES.get(template_key, DIGEST_TEMPLATES["default"])

        # Build digest prompt
        articles_text = []
        for art in new_articles:
            # Format publish time
            pub_time_str = ""
            ts = art.pub_time or art.timestamp
            if ts:
                try:
                    from datetime import datetime as _dt
                    pub_time_str = _dt.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    pub_time_str = ""
            article_text = f"### {art.title}\n来源: {art.source_name}\n发布时间: {pub_time_str}\n"

            if scrape_full and art.url and "mp.weixin.qq.com" in art.url:
                try:
                    content = fetch_article_content(art.url)
                    if content:
                        if len(content) > max_content_chars:
                            content = content[:max_content_chars] + f"\n...(原文{len(content)}字，已截断)"
                        article_text += f"\n{content}\n"
                        logger.info(
                            "[OA-DIGEST] Article '%s': scraped OK, content_len=%d, url=%s",
                            art.title[:30], len(content), art.url[:80],
                        )
                    else:
                        # Fallback to digest
                        article_text += f"\n摘要: {art.digest}\n"
                        logger.warning(
                            "[OA-DIGEST] Article '%s': scraped empty (url=%s), using digest fallback",
                            art.title[:30], art.url[:80],
                        )
                except Exception as e:
                    logger.warning("[OA-DIGEST] Article '%s': scrape failed (%s, url=%s), using digest", art.title[:30], e, art.url[:80])
                    article_text += f"\n摘要: {art.digest}\n"
            else:
                # Non-WeChat URL or no URL — use digest directly
                logger.info(
                    "[OA-DIGEST] Article '%s': using digest (url=%s)",
                    art.title[:30], (art.url or "none")[:80],
                )
                article_text += f"\n摘要: {art.digest}\n"

            article_text += f"\n链接: {art.url}\n"
            articles_text.append(article_text)

        full_prompt = f"请对以下 {len(new_articles)} 篇公众号文章进行摘要：\n\n" + "\n---\n".join(articles_text)

        # Call LLM
        logger.info(
            "[OA-DIGEST] Calling LLM: %d articles, prompt=%d chars, template=%s",
            len(new_articles), len(full_prompt),
            "custom_prompt" if group.custom_prompt else (group.digest_template or "default"),
        )
        digest_text = call_llm(full_prompt, system_prompt, summarizer=self._summarizer)

        # Mark as digested
        for art in new_articles:
            self._history.mark_digested(art.url)

        logger.info(
            "[OA-DIGEST] Digest generated for group '%s': %d chars, %d articles covered",
            group.name, len(digest_text), len(new_articles),
        )

        return {
            "success": True,
            "group_id": group_id,
            "articles_count": len(new_articles),
            "digest_text": digest_text,
            "errors": [],
        }

    @staticmethod
    def _calc_effective_lookback(group) -> int:
        """Calculate effective lookback hours based on mode and schedule.

        For 'auto' mode, estimate the interval from the cron_expr and add
        a 1-hour buffer. For 'manual' mode, use lookback_hours directly.
        """
        if group.lookback_mode == "manual":
            logger.info(
                "[OA-DIGEST] Lookback calc: mode=manual, result=%dh",
                group.lookback_hours,
            )
            return group.lookback_hours

        # Auto mode: derive from cron_expr
        cron_expr = group.cron_expr
        if not cron_expr:
            # No schedule (manual trigger) — default 24h
            return 24

        # Try to parse hours from cron expressions (5-field: min hour dom mon dow)
        hours = set()
        for line in cron_expr.strip().split('\n'):
            parts = line.strip().split()
            if len(parts) >= 2:
                hour_part = parts[1]
                for h in hour_part.split(","):
                    h = h.strip()
                    if h.startswith("*/"):
                        # Step expression: */6 → 0, 6, 12, 18
                        try:
                            step = int(h[2:])
                            if step > 0:
                                for hh in range(0, 24, step):
                                    hours.add(hh)
                        except ValueError:
                            pass
                    elif "-" in h and not h.startswith("-"):
                        # Range expression: 9-17 → 9, 10, ..., 17
                        try:
                            lo, hi = h.split("-", 1)
                            for hh in range(int(lo), int(hi) + 1):
                                hours.add(hh)
                        except ValueError:
                            pass
                    else:
                        try:
                            hours.add(int(h))
                        except ValueError:
                            pass

        logger.info(
            "[OA-DIGEST] Lookback calc: mode=auto, cron='%s', parsed_hours=%s",
            cron_expr, sorted(hours) if hours else "none",
        )

        if len(hours) >= 2:
            # Multiple times per day — find minimum gap
            sorted_hours = sorted(hours)
            gaps = []
            for i in range(1, len(sorted_hours)):
                gaps.append(sorted_hours[i] - sorted_hours[i - 1])
            gaps.append(24 - sorted_hours[-1] + sorted_hours[0])
            min_gap = min(gaps)
            result = min_gap + 1  # +1h buffer
            logger.info("[OA-DIGEST] Lookback calc: min_gap=%dh, result=%dh", min_gap, result)
            return result
        elif len(hours) == 1:
            # Once per day
            result = 24 + 1  # 25h buffer
            logger.info("[OA-DIGEST] Lookback calc: once/day, result=%dh", result)
            return result
        else:
            # Could not parse — default
            logger.info("[OA-DIGEST] Lookback calc: could not parse hours, default=24h")
            return 24

    def search_articles(self, keyword: str, limit: int = 50) -> list[OAArticle]:
        """Search OA articles by keyword across all groups.

        Args:
            keyword: Search keyword
            limit: Max results

        Returns:
            List of matching OAArticle objects
        """
        if not self._client:
            return []

        # Get all OA sessions
        oa_sessions = get_oa_sessions(self._client)
        results = []

        for session in oa_sessions:
            gh_id = session.get("username", "")
            try:
                articles = fetch_oa_articles(self._client, gh_id, limit=20)
                for art in articles:
                    if keyword.lower() in art.title.lower() or keyword.lower() in art.digest.lower():
                        results.append(art)
                        if len(results) >= limit:
                            return results
            except Exception as e:
                logger.debug(f"Search failed for {gh_id}: {e}")

        return results
