"""Regression tests for the unified OA discovery/job pipeline."""

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.assistant.config import AssistantConfig, OAMonitorGroup
from src.db.content_cache import ContentCache
from src.assistant.oa_parser import OAArticle


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
