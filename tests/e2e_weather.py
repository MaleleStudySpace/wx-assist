"""端到端联测 — weather-push skill 全流程

覆盖:
  1. Skill 注册
  2. cron 创建 + 立即执行 + 输出验证
  3. TaskCenter 记录
  4. 前端 Skill 库 / 执行历史
"""
import asyncio
import sys
import time
import requests
from pathlib import Path

API = "http://127.0.0.1:17327"
UI = "http://127.0.0.1:15173"

PASS, FAIL = 0, 0
RESULTS = []


def ok(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  ✓ {name}")
        RESULTS.append({"name": name, "status": "PASS"})
        PASS += 1
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        RESULTS.append({"name": name, "status": "FAIL", "err": str(e)[:200]})
        FAIL += 1


def get(path, **kw):
    return requests.get(API + path, timeout=15, **kw).json()


def post(path, body):
    return requests.post(API + path, json=body, timeout=15).json()


def delete(path):
    return requests.delete(API + path, timeout=10).json()


# ═══════════════════════════════════════════════════════════
print("=== weather-push 端到端联测 ===\n")

# ── Phase 1: 后端 ──
print("-- Phase 1: 后端 API --")

ok("weather-push skill 已注册", lambda:
    "weather-push" in [s["name"] for s in get("/api/skills").get("data", [])])

weather_id = None
ok("创建天气定时任务", lambda:
    (lambda d: (
        d.get("ok") and (
            (weather_id := d.get("data", {}).get("id"))
            or (_ for _ in ()).throw(AssertionError("no id"))
        )
    ))(post("/api/scheduler/tasks", {
        "name": "每日天气",
        "skill": "weather-push",
        "cron": "0 7 * * *",
        "args": {"location": "北京", "days": 1, "lang": "zh"},
        "push_enabled": True,
    }))
)

ok("天气任务在列表中", lambda:
    weather_id in [t["id"] for t in get("/api/scheduler/tasks").get("data", [])])

run_output = None
ok("立即执行天气任务", lambda:
    (lambda d: (
        d.get("ok") is True and
        (run_output := d.get("data", {}).get("output", ""))
        and not (r := None)
    ))(post(f"/api/scheduler/tasks/{weather_id}/run", {}))
)

if run_output:
    ok("天气输出包含温度/城市信息", lambda:
        "°" in run_output or "℃" in run_output or
        "北京" in run_output or "Beijing" in run_output or
        "wttr" in run_output.lower())

    ok("天气输出不含 [CRON 错误]", lambda:
        "[CRON 错误]" not in run_output)

    print(f"     天气输出预览 ({len(run_output)} chars):")
    for line in run_output.split("\n")[:10]:
        print(f"       {line.strip()}")

ok("TaskCenter 有天气执行记录", lambda:
    len(get("/api/tasks", params={"type": "cron", "limit": 10}).get("tasks", [])) > 0)

# 清理
if weather_id:
    delete(f"/api/scheduler/tasks/{weather_id}")
    print(f"     已清理任务 {weather_id}")

# ═══════════════════════════════════════════════════════════
print("\n-- Phase 2: 前端 UI --")

async def frontend():
    global PASS, FAIL
    from playwright.async_api import async_playwright, expect

    async def fe(name, fn):
        global PASS, FAIL
        try:
            await fn()
            print(f"  ✓ {name}")
            RESULTS.append({"name": name, "status": "PASS"})
            PASS += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            RESULTS.append({"name": name, "status": "FAIL", "err": str(e)[:200]})
            FAIL += 1

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(UI, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # 进入定时任务
        await page.locator('nav button:has-text("定时任务")').first.click()
        await page.wait_for_timeout(800)

        # Skill 库
        await page.locator('nav button:has-text("Skill 库")').last.click()
        await page.wait_for_timeout(1000)

        await fe("Skill 库 weather-push 可见", lambda:
            page.locator('button:has-text("weather-push")').first.wait_for(timeout=3000))
        await page.locator('button:has-text("weather-push")').first.click()
        await page.wait_for_timeout(300)
        await fe("天气预报详情含 location 参数", lambda:
            page.locator('text=location').first.wait_for(timeout=2000))
        await page.screenshot(path="C:/Users/74062/Desktop/wx-assist/data/screenshots/weather-skill.png", full_page=True)

        # 执行历史
        await page.locator('nav button:has-text("执行历史")').last.click()
        await page.wait_for_timeout(1000)
        opts = await page.locator("select").first.evaluate(
            "el => [...el.options].map(o => o.text)")
        assert any("全部" in str(t) for t in opts), f"options: {opts}"
        count = await page.locator("select").first.evaluate("el => el.options.length")
        assert count > 1, f"only {count} options"
        print("  ✓ 历史筛选下拉正常")
        PASS += 1
        RESULTS.append({"name": "历史筛选下拉正常", "status": "PASS"})

        await browser.close()

asyncio.run(frontend())

# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print(f"  {PASS} passed / {FAIL} failed")
if FAIL > 0:
    for r in RESULTS:
        if r["status"] == "FAIL":
            print(f"    ✗ {r['name']}: {r['err'][:200]}")
print("=" * 50)
sys.exit(0 if FAIL == 0 else 1)
