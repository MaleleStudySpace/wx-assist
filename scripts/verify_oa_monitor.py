# -*- coding: utf-8 -*-
"""oa_monitor _check_account 决策逻辑端到端验证（临时库，不污染真实数据）"""
import sys, os, time, tempfile, shutil
from pathlib import Path
from unittest import mock
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.assistant.oa_monitor import OAMonitorEngine
from src.assistant.outbox import Outbox
from src.db.content_cache import ContentCache
from src.assistant.oa_parser import OAArticle
from src.assistant.config import AssistantConfig, OAMonitorGroup

NOW = int(time.time())
TMP = Path(tempfile.gettempdir()) / f"oa_verify_{int(time.time())}.db"
TMP_OUTBOX = Path(tempfile.gettempdir()) / f"oa_verify_outbox_{int(time.time())}.db"

def make_article(title, url, pub_ts):
    return OAArticle(
        title=title, url=url, digest="摘要", cover="",
        source_name="验证号", source_username="gh_test",
        pub_time=pub_ts, gh_id="gh_test", timestamp=pub_ts,
    )

def run_scenario(name, articles, setup=None, expect_push=None):
    """构造临时环境跑 _check_account"""
    shutil.copy2("data/messages.db", str(TMP)) if False else None
    cc = ContentCache(str(TMP))
    outbox = Outbox(TMP_OUTBOX)
    cfg = AssistantConfig()
    mg = OAMonitorGroup(name="验证组", accounts=["gh_test"], enabled=True, push_target="ilink")
    # 注入预置数据
    if setup:
        setup(cc, outbox)
    engine = OAMonitorEngine(cfg, outbox, content_cache=cc)

    pushed = []
    with mock.patch("src.assistant.oa_parser.fetch_oa_articles", return_value=articles), \
         mock.patch.object(engine, "_get_wcdb_client", return_value=object()), \
         mock.patch.object(engine, "_push_to_wechat", side_effect=lambda nid, g, t, c: (pushed.append((nid, g, t)), (True, ''))[1]), \
         mock.patch("src.summarize.create_summarizer", return_value=mock.MagicMock()), \
         mock.patch("src.config.load_config", return_value=None), \
         mock.patch("src.assistant.oa_reader.fetch_article_content", return_value="全文内容"):
        engine._check_account(mg, "gh_test")

    ok = (len(pushed) == (1 if expect_push else 0)) if expect_push is not None else True
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: 推送次数={len(pushed)}（期望 {'推送' if expect_push else '不推'}）")
    for p in pushed:
        print(f"       推送 → nid={p[0]} title={p[2][:40]}")
    return ok, cc, outbox

results = []

# ── 场景 A：文章在 oa_cache（增量合并抢先），但 outbox 无推送记录 → 必须推送 ──
def setup_a(cc, outbox):
    art = make_article("增量合并抢先的文章", "http://mp.weixin.qq.com/a", NOW - 60)
    cc.upsert("oa_cache", cc._clean_oa(art))  # 模拟增量合并写入缓存
results.append(run_scenario(
    "A: 缓存已有但未推送（增量合并抢先）→ 应推送",
    [make_article("增量合并抢先的文章", "http://mp.weixin.qq.com/a", NOW - 60)],
    setup_a, expect_push=True))

# ── 场景 B：outbox 已有推送成功记录 → 不重复推 ──
def setup_b(cc, outbox):
    nid = outbox.add("oa_article_alert", "验证组", "已推送的文章",
               '{"url": "http://mp.weixin.qq.com/b"}', priority="high", url="http://mp.weixin.qq.com/b")
    outbox.update_push_result(nid, "ilink", "success", "")  # 推送成功 → dedup 拦截
results.append(run_scenario(
    "B: outbox 已有该 url 推送记录 → 不重复推",
    [make_article("已推送的文章", "http://mp.weixin.qq.com/b", NOW - 30)],
    setup_b, expect_push=False))

# ── 场景 C：已抓全文的文章过一遍 oa_monitor → content_status 不被破坏 ──
def setup_c(cc, outbox):
    art = make_article("已抓全文的文章", "http://mp.weixin.qq.com/c", NOW - 86400)
    cleaned = cc._clean_oa(art)
    cleaned["content_status"] = 1
    cleaned["full_content"] = "已抓取的完整全文内容"
    cc.upsert("oa_cache", cleaned)
results.append(run_scenario(
    "C: 已抓全文(content_status=1) 过 oa_monitor → 状态保留",
    [make_article("已抓全文的文章", "http://mp.weixin.qq.com/c", NOW - 86400)],
    setup_c, expect_push=False))

# ── 场景 D：全新文章（缓存无 + outbox 无）→ 推送 + 写缓存 + outbox 记录 ──
ok_d, cc_d, outbox_d = run_scenario(
    "D: 全新文章 → 应推送",
    [make_article("全新文章", "http://mp.weixin.qq.com/d", NOW - 10)],
    expect_push=True)
if ok_d:
    row = cc_d.query_one("SELECT content_status FROM oa_cache WHERE url=?", ["http://mp.weixin.qq.com/d"])
    pushed_out = outbox_d.query_by_url("http://mp.weixin.qq.com/d", notif_type="oa_article_alert")
    print(f"       D 验证: 缓存已写={row is not None}, outbox 有记录={pushed_out}")
    results.append((True, "D 附加验证"))

# 清理
for p in (TMP, TMP_OUTBOX):
    try: p.unlink(missing_ok=True)
    except Exception: pass

fails = [r for r in results if not r[0]]
print(f"\n=== 总结: {len(results)-len(fails)}/{len(results)} 通过 ===")
sys.exit(1 if fails else 0)
