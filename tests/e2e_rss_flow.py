"""端到端深度联测 — 创建 rss-fetch skill → 创建 cron → 执行 → 验证 → UI 确认

覆盖:
  1. Skill 注册 (API)
  2. Cron 任务创建 (API)
  3. 立即执行 (API)
  4. 输出验证 (含 [SILENT] 处理)
  5. TaskCenter 追踪 (API)
  6. Frontend: Skill 库浏览
  7. Frontend: 任务列表 + 新建表单 + cron 预设
  8. Frontend: 执行历史
"""
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

import requests

API = "http://127.0.0.1:17327"
UI = "http://127.0.0.1:15173"
SCREENSHOT_DIR = Path("C:/Users/74062/Desktop/wx-assist/data/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

PASS = 0; FAIL = 0; RESULTS = []

RSS_URL = "https://feeds.bbci.co.uk/news/rss.xml"

# ── 测试夹具：会在测试中创建的 cron ID，测试完毕清理 ──
CRON_ID = None


def ok(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  ✓ {name}")
        RESULTS.append({"name": name, "status": "PASS"})
        PASS += 1
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        RESULTS.append({"name": name, "status": "FAIL", "err": str(e)[:300]})
        FAIL += 1


def get_json(path, **kw):
    r = requests.get(API + path, **kw, timeout=10)
    return r.json()


def post_json(path, body):
    r = requests.post(API + path, json=body, timeout=10)
    return r.json()


def delete_req(path):
    r = requests.delete(API + path, timeout=10)
    return r.json()


# ════════════════════════════════════════════════════════════
print("=== 端到端深度联测 — RSS skill 全场景 ===\n")

# ─── Phase 1: 后端 API ───
print("── Phase 1: 后端全链路 ──")

# 1.1 Skill 列表
ok("GET /api/skills 含 rss-fetch", lambda: (
    lambda d: (None if "rss-fetch" in [s["name"] for s in d["data"]]
               else (_ for _ in ()).throw(
                   AssertionError(f"rss-fetch not found in skills: {[s['name'] for s in d['data']]}"))
               )
    )(get_json("/api/skills"))
)

# 1.2 创建 cron 任务
ok("POST /api/scheduler/tasks 创建成功", lambda: (
    lambda data: (
        (data["ok"] is True and data.get("data", {}).get("id") is not None) or
        (_ for _ in ()).throw(AssertionError(f"create failed: {data}"))
    ) and globals().__setitem__("CRON_ID", data["data"]["id"])
    or print(f"     创建: {CRON_ID}")
)(post_json("/api/scheduler/tasks", {
    "name": "E2E_RSS_联测",
    "skill": "rss-fetch",
    "cron": "0 9 * * *",
    "args": {"url": RSS_URL, "max_items": 5},
    "push_enabled": True,
    "push_target": "ilink",
})))

# 1.3 任务在列表中
ok("GET /api/scheduler/tasks 含 E2E 任务", lambda: (
    lambda d: (None if CRON_ID in [t["id"] for t in d.get("data", [])]
               else (_ for _ in ()).throw(AssertionError(f"{CRON_ID} not found in tasks")))
)(get_json("/api/scheduler/tasks")))

# 1.4 立即执行
run_output = None
ok("POST /api/scheduler/tasks/:id/run 执行成功", lambda: (
    lambda data: (
        (data["ok"] is True) or (_ for _ in ()).throw(
            AssertionError(f"run failed: {data}"))
    ) and globals().__setitem__("run_output", data.get("data", {}).get("output", ""))
    or print(f"     输出长度: {len(globals().get('run_output', '') or '')}")
)(post_json(f"/api/scheduler/tasks/{CRON_ID}/run", {})))

# 1.5 输出验证
if run_output:
    ok("执行输出有内容", lambda: (
        len(run_output) > 10 or (_ for _ in ()).throw(
            AssertionError(f"output too short: {run_output[:100]}"))
    ))
    ok("输出不含原始错误", lambda: (
        "[CRON 错误]" not in run_output or (_ for _ in ()).throw(
            AssertionError(f"output has error"))
    ))
    ok("输出含 RSS 文章或正常标记", lambda: (
        "[SILENT]" in run_output or "http" in run_output or "📡" in run_output or len(run_output) > 50
        or (_ for _ in ()).throw(AssertionError(f"output content odd: {run_output[:150]}"))
    ))
    print(f"     输出预览: {run_output[:200]}")

# 1.6 TaskCenter 追踪
ok("TaskCenter 有 cron 型记录", lambda: (
    lambda d: (
        len(d.get("tasks", [])) > 0 or (_ for _ in ()).throw(
            AssertionError("no cron tasks in task center"))
    )
)(get_json("/api/tasks", params={"task_type": "cron", "limit": 5})))

# 1.7 清理
if CRON_ID:
    delete_req(f"/api/scheduler/tasks/{CRON_ID}")
    print(f"     已清理任务 {CRON_ID}")


# ─── Phase 2: 前端 UI ───
print("\n── Phase 2: 前端 UI 验证 ──")

async def phase2():
    global PASS, FAIL, RESULTS
    from playwright.async_api import async_playwright, expect

    async def ok_async(name, fn):
        global PASS, FAIL
        try:
            await fn()
            print(f"  ✓ {name}")
            RESULTS.append({"name": name, "status": "PASS"})
            PASS += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            RESULTS.append({"name": name, "status": "FAIL", "err": str(e)[:300]})
            FAIL += 1
        try:
            await fn()
            print(f"  ✓ {name}")
            RESULTS.append({"name": name, "status": "PASS"})
            PASS += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            RESULTS.append({"name": name, "status": "FAIL", "err": str(e)[:300]})
            FAIL += 1

    async def ss(name):
        path = SCREENSHOT_DIR / f"{name}.png"
        await page.screenshot(path=str(path), full_page=True)
        print(f"  📷 {name}.png")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(UI, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        # 展开侧边栏 定时任务
        btn_定时 = page.locator('nav button:has-text("定时任务")').first
        await btn_定时.click()
        await page.wait_for_timeout(800)
        await ss("phase2-subnav")

        # 2.1 Skill 库
        skill_入口 = page.locator('nav button:has-text("Skill 库")').last
        await skill_入口.click()
        await page.wait_for_timeout(1000)

        await ok_async("UI Skill库 含 rss-fetch", lambda: (
            page.locator('button:has-text("rss-fetch")').first.wait_for(timeout=3000)
        ))
        await page.locator('button:has-text("rss-fetch")').first.click()
        await page.wait_for_timeout(300)
        await ok_async("UI Skill库 显示描述", lambda: (
            page.locator('text=抓取 RSS 源').first.wait_for(timeout=2000)
        ))
        await ok_async("UI Skill库 显示参数 url", lambda: (
            page.locator('text=url').first.wait_for(timeout=2000)
        ))
        await ok_async("UI Skill库 显示参数 max_items", lambda: (
            page.locator('text=max_items').first.wait_for(timeout=2000)
        ))
        await ss("phase2-skill-rss")

        # 2.2 执行历史
        历史_入口 = page.locator('nav button:has-text("执行历史")').last
        await 历史_入口.click()
        await page.wait_for_timeout(1000)
        await ok_async("UI 历史: 过滤按钮可见", lambda: (
            page.locator('button:has-text("全部")').first.wait_for(timeout=2000)
        ))
        await ss("phase2-history")

        # 2.3 回任务列表
        任务_入口 = page.locator('nav button:has-text("定时任务")').last
        await 任务_入口.click()
        await page.wait_for_timeout(800)
        await ok_async("UI 任务列表: '共 N 个任务'", lambda: (
            page.locator('text=/共 \\d+ 个任务/').first.wait_for(timeout=2000)
        ))

        # 2.4 点新建任务
        await page.locator('button:has-text("新建任务")').first.click()
        await page.wait_for_timeout(600)
        await ok_async("UI 新建: 表单打开", lambda: (
            page.locator('input[placeholder*="36氪"]').first.wait_for(timeout=2000)
        ))

        opts = await page.locator("select").first.evaluate(
            "el => [...el.options].map(o => o.value)"
        )
        assert "rss-fetch" in opts, f"rss-fetch not in {opts}"
        print("  ✓ UI 新建: skill select 含 rss-fetch")
        PASS += 1
        RESULTS.append({"name": "UI 新建: skill select 含 rss-fetch", "status": "PASS"})

        # cron 预设
        for preset in ["每分钟", "每5分钟", "每30分钟", "每小时",
                       "每天8点", "每天18点", "工作日9点"]:
            await ok_async(f"UI 新建: 预设『{preset}』可见", lambda p=preset: (
                page.locator(f'button:has-text("{p}")').first.wait_for(timeout=2000)
            ))

        # 点每天8点
        await page.locator('button:has-text("每天8点")').first.click()
        await page.wait_for_timeout(200)
        cron_val = await page.locator("textarea").first.evaluate("el => el.value")
        assert cron_val == "0 8 * * *", f"expected '0 8 * * *', got '{cron_val}'"
        print("  ✓ UI 新建: 预设填入 '0 8 * * *'")
        PASS += 1
        RESULTS.append({"name": "UI 新建: 预设填入 '0 8 * * *'", "status": "PASS"})
        await page.locator('button:has-text("取消")').first.click()
        await page.wait_for_timeout(300)
        await browser.close()

asyncio.run(phase2())

# ════════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print(f"  结果: {PASS} passed / {FAIL} failed")
if FAIL > 0:
    print("\n  失败列表:")
    for r in RESULTS:
        if r["status"] == "FAIL":
            print(f"    ✗ {r['name']}: {r['err'][:200]}")
print("=" * 50)
sys.exit(0 if FAIL == 0 else 1)
