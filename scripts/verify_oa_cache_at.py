# -*- coding: utf-8 -*-
"""验证 _cache_article 已缓存跳过：多轮轮询后 cached_at 不被刷新、RAG 触发收敛"""
import sys, os, time, tempfile
from pathlib import Path
from unittest import mock
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.assistant.oa_monitor import OAMonitorEngine
from src.assistant.outbox import Outbox
from src.db.content_cache import ContentCache
from src.assistant.oa_parser import OAArticle
from src.assistant.config import AssistantConfig, OAMonitorGroup

TMP = Path(tempfile.gettempdir()) / f"oa_verify2_{int(time.time())}.db"
TMP_OUTBOX = Path(tempfile.gettempdir()) / f"oa_verify2_outbox_{int(time.time())}.db"

cc = ContentCache(str(TMP))
outbox = Outbox(TMP_OUTBOX)
cfg = AssistantConfig()
mg = OAMonitorGroup(name="验证组", accounts=["gh_test"], enabled=True, push_target="ilink")
engine = OAMonitorEngine(cfg, outbox, content_cache=cc)

# 预置：2 篇已缓存文章（一篇已抓全文 content_status=1，一篇待抓 0）
now = int(time.time())
art1 = OAArticle(title="已缓存文章1", url="http://mp.weixin.qq.com/1", digest="d",
                 source_name="验证号", source_username="gh_test", pub_time=now - 3600, gh_id="gh_test", timestamp=now - 3600)
art2 = OAArticle(title="已缓存文章2", url="http://mp.weixin.qq.com/2", digest="d",
                 source_name="验证号", source_username="gh_test", pub_time=now - 7200, gh_id="gh_test", timestamp=now - 7200)
c1 = cc._clean_oa(art1); c1["content_status"] = 1; c1["full_content"] = "已抓全文内容" * 100; c1["cached_at"] = now - 300
c2 = cc._clean_oa(art2); c2["content_status"] = 0; c2["full_content"] = ""; c2["cached_at"] = now - 600
cc.upsert("oa_cache", c1); cc.upsert("oa_cache", c2)

# 记录轮询前的 cached_at 快照
snap_before = {r["url"]: r["cached_at"] for r in cc.query("SELECT url, cached_at FROM oa_cache")}
print("轮询前 cached_at:", {k[-1:]: v for k, v in snap_before.items()})

# 模拟 3 轮 poll（每轮 2 篇文章都从 WCDB 拉出来）
rag_calls = []
articles = [art1, art2]
with mock.patch("src.assistant.oa_parser.fetch_oa_articles", return_value=articles), \
     mock.patch.object(engine, "_get_wcdb_client", return_value=object()), \
     mock.patch.object(engine, "_push_to_wechat", return_value=None), \
     mock.patch("src.web.server.get_rag_engine", return_value=object()), \
     mock.patch("src.assistant.oa_monitor.logger") as mlog:
    for rnd in range(3):
        engine._poll_cycle()

# 轮询后的 cached_at
snap_after = {r["url"]: r["cached_at"] for r in cc.query("SELECT url, cached_at FROM oa_cache")}
print("轮询后 cached_at:", {k[-1:]: v for k, v in snap_after.items()})

# 验证
cached_at_same = snap_before == snap_after
print(f"cached_at 未被刷新: {'PASS ✅' if cached_at_same else 'FAIL ❌'}")
status_ok = cc.query_one("SELECT content_status FROM oa_cache WHERE url=?", ["http://mp.weixin.qq.com/1"])["content_status"] == 1
print(f"已抓文章 content_status 保留 1: {'PASS ✅' if status_ok else 'FAIL ❌'}")

for p in (TMP, TMP_OUTBOX):
    try: p.unlink(missing_ok=True)
    except Exception: pass

sys.exit(0 if cached_at_same and status_ok else 1)
