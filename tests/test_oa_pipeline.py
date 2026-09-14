"""Regression tests for the unified OA discovery/job pipeline."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.assistant.config import AssistantConfig, OAMonitorGroup
from src.assistant.oa_parser import OAArticle
from src.db.content_cache import ContentCache


class TestOAPipeline(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache = ContentCache(str(Path(self.tmpdir.name) / "messages.db"))
        self.cache.upsert("oa_accounts", {
            "gh_id": "gh_test",
            "display_name": "测试公众号",
            "avatar_url": "",
            "last_updated": 1,
        })

    def tearDown(self):
        self.cache.stop_oa_content_fetcher()
        self.tmpdir.cleanup()

    def _config(self, enabled=True):
        return AssistantConfig(
            assistant_enabled=True,
            oa_monitor_groups=[OAMonitorGroup(
                id="oam_test", name="测试监控", accounts=["gh_test"], enabled=enabled,
            )],
        )

    @patch("src.assistant.oa_parser.fetch_oa_articles")
    def test_scan_creates_cache_and_two_idempotent_jobs(self, fetch):
        fetch.return_value = [OAArticle(
            title="新文章", url="https://example.test/a", digest="短摘要",
            source_name="测试公众号", gh_id="gh_test", timestamp=int(time.time()),
        )]
        client = object()
        self.assertEqual(self.cache.scan_oa_incremental(client, self._config()), 1)
        self.assertEqual(self.cache.scan_oa_incremental(client, self._config()), 0)
        rows = self.cache.query(
            "SELECT kind, state FROM oa_jobs WHERE url=? ORDER BY kind",
            ["https://example.test/a"],
        )
        self.assertEqual([(r["kind"], r["state"]) for r in rows], [
            ("full_text", "pending"), ("instant_alert", "pending"),
        ])
        self.assertEqual(fetch.call_count, 2)

    @patch("src.assistant.oa_parser.fetch_oa_articles")
    def test_disabled_monitor_still_caches_but_does_not_alert(self, fetch):
        fetch.return_value = [OAArticle(
            title="缓存文章", url="https://example.test/cache", digest="摘要",
            source_name="测试公众号", gh_id="gh_test", timestamp=int(time.time()),
        )]
        self.assertEqual(self.cache.scan_oa_incremental(
            object(), self._config(enabled=False)), 1)
        rows = self.cache.query(
            "SELECT kind FROM oa_jobs WHERE url=? ORDER BY kind",
            ["https://example.test/cache"],
        )
        self.assertEqual([r["kind"] for r in rows], ["full_text"])
        self.assertIsNotNone(self.cache.query_one(
            "SELECT url FROM oa_cache WHERE url=?", ["https://example.test/cache"]
        ))

    @patch("src.assistant.oa_reader.fetch_article_content")
    def test_fulltext_worker_consumes_job_and_updates_cache(self, fetch):
        fetch.return_value = "正文内容"
        article = {
            "url": "https://example.test/full", "gh_id": "gh_test",
            "title": "全文", "digest": "摘要", "source_name": "测试公众号",
        }
        self.cache.upsert("oa_cache", {
            "url": article["url"], "gh_id": article["gh_id"], "title": article["title"],
            "digest": article["digest"], "cover_url": "", "source_name": article["source_name"],
            "pub_time": 0, "full_content": "", "content_status": 0,
            "llm_summary": "", "llm_summary_ok": 0, "cached_at": 1,
        })
        self.cache._ensure_oa_job("full_text", article)
        self.cache._fetch_one_content_job()
        row = self.cache.query_one(
            "SELECT full_content, content_status FROM oa_cache WHERE url=?",
            [article["url"]],
        )
        self.assertEqual(row["full_content"], "正文内容")
        self.assertEqual(row["content_status"], 1)

    @patch("src.assistant.oa_reader.fetch_article_content")
    def test_fulltext_worker_reuses_existing_body_without_http(self, fetch):
        article = {
            "url": "https://example.test/already-cached", "gh_id": "gh_test",
            "title": "已有全文", "digest": "摘要", "source_name": "测试公众号",
        }
        self.cache.upsert("oa_cache", {
            "url": article["url"], "gh_id": article["gh_id"], "title": article["title"],
            "digest": article["digest"], "cover_url": "", "source_name": article["source_name"],
            "pub_time": 0, "full_content": "已经由即时提醒抓到的正文", "content_status": 2,
            "llm_summary": "", "llm_summary_ok": 0, "cached_at": 1,
        })
        self.cache._ensure_oa_job("full_text", article)

        self.cache._fetch_one_content_job()

        fetch.assert_not_called()
        row = self.cache.query_one(
            "SELECT full_content, content_status FROM oa_cache WHERE url=?",
            [article["url"]],
        )
        self.assertEqual(row["full_content"], "已经由即时提醒抓到的正文")
        self.assertEqual(row["content_status"], 2)
        job = self.cache.query_one(
            "SELECT state FROM oa_jobs WHERE kind='full_text' AND url=?",
            [article["url"]],
        )
        self.assertEqual(job["state"], "completed")

    def test_fulltext_write_does_not_overwrite_existing_body(self):
        url = "https://example.test/race"
        self.cache.upsert("oa_cache", {
            "url": url, "gh_id": "gh_test", "title": "竞态", "digest": "摘要",
            "cover_url": "", "source_name": "测试公众号", "pub_time": 0,
            "full_content": "先到的完整正文", "content_status": 2,
            "llm_summary": "", "llm_summary_ok": 0, "cached_at": 1,
        })

        self.assertFalse(self.cache._store_oa_full_content(url, "后到的正文", status=1))
        row = self.cache.query_one(
            "SELECT full_content, content_status FROM oa_cache WHERE url=?", [url]
        )
        self.assertEqual(row["full_content"], "先到的完整正文")
        self.assertEqual(row["content_status"], 2)

    @patch("src.assistant.oa_reader.fetch_article_content")
    def test_alert_uses_job_digest_without_wcdb_retry(self, fetch):
        from unittest.mock import MagicMock
        from src.assistant.oa_monitor import OAMonitorEngine

        outbox = MagicMock()
        outbox.query_by_url.return_value = False
        outbox.get_by_url.return_value = None
        outbox.add.return_value = 1
        config = self._config()
        engine = OAMonitorEngine(config, outbox, content_cache=self.cache)
        article = {
            "url": "https://example.test/no-body", "gh_id": "gh_test",
            "title": "没有正文", "digest": "微信原始摘要", "source_name": "测试公众号",
        }
        self.cache._ensure_oa_job("instant_alert", article, group={
            "id": "oam_test", "name": "测试监控", "custom_prompt": "",
        })
        job = self.cache.claim_oa_job("instant_alert")
        self.assertIsNotNone(job)
        group = config.oa_monitor_groups[0]
        summarizer = MagicMock()
        summarizer.chat.return_value = "基于摘要生成的即时内容"
        fetch.return_value = ""
        with patch.object(engine, "_get_wcdb_client") as get_wcdb, \
             patch.object(engine, "_push_to_wechat", return_value=("skipped", "")), \
             patch("src.config.load_config", return_value=None), \
             patch("src.summarize.create_summarizer", return_value=summarizer):
            engine._process_alert_job(job, group, config)

        get_wcdb.assert_not_called()
        fetch.assert_called_once_with(article["url"], timeout=15, title=article["title"])
        self.assertEqual(summarizer.chat.call_count, 1)
        notification = outbox.add.call_args.args[3]
        self.assertIn("基于摘要生成的即时内容", notification)

    @patch("src.assistant.oa_parser.fetch_oa_articles")
    def test_full_sync_backfills_fulltext_job(self, fetch):
        fetch.return_value = []
        self.cache.upsert("oa_cache", {
            "url": "https://example.test/legacy", "gh_id": "gh_test", "title": "旧文",
            "digest": "摘要", "cover_url": "", "source_name": "测试公众号", "pub_time": 1,
            "full_content": "", "content_status": 0, "llm_summary": "", "llm_summary_ok": 0,
            "cached_at": 1,
        })
        self.cache.start_oa_content_fetcher()
        self.cache.stop_oa_content_fetcher()
        row = self.cache.query_one(
            "SELECT kind FROM oa_jobs WHERE url=?", ["https://example.test/legacy"]
        )
        self.assertEqual(row["kind"], "full_text")

    def test_claim_lease_and_retry(self):
        article = {
            "url": "https://example.test/retry", "gh_id": "gh_test",
            "title": "重试", "digest": "摘要", "source_name": "测试公众号",
        }
        self.cache._ensure_oa_job("instant_alert", article)
        job = self.cache.claim_oa_job("instant_alert", lease_seconds=30)
        self.assertIsNotNone(job)
        self.assertFalse(self.cache.finish_oa_job(job["id"], "wrong", "sent"))
        self.assertTrue(self.cache.finish_oa_job(
            job["id"], job["lease_token"], "retry", "暂时失败", delay=0
        ))
        retry = self.cache.claim_oa_job("instant_alert", lease_seconds=30)
        self.assertIsNotNone(retry)
        self.assertEqual(retry["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
