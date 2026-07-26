"""SchedulerPanel 端到端测试 — playwright 驱动浏览器"""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright, Page, expect

URL = "http://127.0.0.1:15173/"
SCREENSHOT_DIR = Path("C:/Users/74062/Desktop/wx-assist/data/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

PASS = 0
FAIL = 0
RESULTS: list = []


async def screenshot(page: Page, name: str) -> str:
    path = SCREENSHOT_DIR / f"{name}.png"
    await page.screenshot(path=str(path), full_page=True)
    return str(path)


async def check(name: str, page: Page, fn):
    """执行 fn(page)，记录结果"""
    global PASS, FAIL
    try:
        await fn(page)
        print(f"  ✓ {name}")
        RESULTS.append({"name": name, "status": "PASS"})
        PASS += 1
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        RESULTS.append({"name": name, "status": "FAIL", "err": str(e)[:200]})
        FAIL += 1


# 让 check 可直接 await: check(name, page, fn) 已被全局 PASS/FAIL 记录
async def run_all_tests(page: Page):
    # ─── Phase 1: Sidebar sub-nav ───
    print("\n-- Phase 1: Sidebar sub-nav --")

    async def _sidebar_visible(p):
        await expect(p.locator('nav button:has-text("定时任务")').first).to_be_visible()
    await check("侧边栏显示『定时任务』入口", page, _sidebar_visible)

    async def _expand_subnav(p):
        btns = p.locator('nav button:has-text("定时任务")')
        await btns.first.click()
        await p.wait_for_timeout(500)
        await expect(p.locator('nav button:has-text("Skill 库")').first).to_be_visible()
    await check("点击侧边栏主『定时任务』展开子 nav", page, _expand_subnav)
    await screenshot(page, "phase1-subnav")

    async def _skill_subnav(p):
        await expect(p.locator('nav button:has-text("Skill 库")').first).to_be_visible()
    await check("子 nav 显示『Skill 库』", page, _skill_subnav)

    async def _history_subnav(p):
        await expect(p.locator('nav button:has-text("执行历史")').first).to_be_visible()
    await check("子 nav 显示『执行历史』", page, _history_subnav)

    # 切换到 Skill 库
    await page.locator('nav button:has-text("Skill 库")').last.click()
    await page.wait_for_timeout(500)

    async def _skill_search(p):
        await expect(p.locator('input[placeholder*="搜索 skill"]')).to_be_visible()
    await check("Skill 库页面：搜索框可见", page, _skill_search)
    await screenshot(page, "phase1-skills")

    # 切换到执行历史
    await page.locator('nav button:has-text("执行历史")').last.click()
    await page.wait_for_timeout(500)

    async def _history_filter(p):
        await expect(p.locator('button:has-text("全部")').first).to_be_visible()
    await check("执行历史页面：过滤按钮『全部』可见", page, _history_filter)
    await screenshot(page, "phase1-history")

    # 回任务页
    await page.locator('nav button:has-text("定时任务")').last.click()
    await page.wait_for_timeout(500)

    # ─── Phase 2: Tasks view ───
    print("\n-- Phase 2: Tasks view --")

    async def _tasks_header(p):
        await expect(p.locator('text=/共 \\d+ 个任务/').first).to_be_visible()
    await check("任务页显示『共 N 个任务』", page, _tasks_header)

    for chip in ["全部", "启用", "暂停"]:
        async def _chip(p, c=chip):
            await expect(p.locator(f'button:has-text("{c}")').first).to_be_visible()
        await check(f"状态 chip『{chip}』可见", page, _chip)

    async def _chip_error(p):
        await expect(p.locator('button:has-text("有失败")').first).to_be_visible()
    await check("状态 chip『⚠ 有失败』可见", page, _chip_error)

    async def _search_box(p):
        await expect(p.locator('input[placeholder*="搜索任务"]')).to_be_visible()
    await check("搜索框存在", page, _search_box)

    # ─── Phase 3: New task form ───
    print("\n-- Phase 3: New task form + cron --")

    # 点新建
    await page.locator('button:has-text("新建任务")').first.click()
    await page.wait_for_timeout(500)

    async def _name_field(p):
        await expect(p.locator('input[placeholder*="36氪"]')).to_be_visible()
    await check("新建表单：任务名称字段", page, _name_field)

    async def _cron_field(p):
        await expect(p.locator('textarea').first).to_be_visible()
    await check("新建表单：Cron textarea", page, _cron_field)
    await screenshot(page, "phase3-form")

    for preset in ["每分钟", "每5分钟", "每30分钟", "每小时",
                    "每天8点", "每天18点", "工作日9点"]:
        async def _preset(p, x=preset):
            await expect(p.locator(f'button:has-text("{x}")').first).to_be_visible()
        await check(f"Cron 预设『{preset}』可见", page, _preset)

    # 点 '每5分钟' 预设
    await page.locator('button:has-text("每5分钟")').first.click()
    await page.wait_for_timeout(200)

    async def _cron_filled(p):
        val = await p.locator("textarea").first.evaluate("el => el.value")
        assert val == "*/5 * * * *", f"expected '*/5 * * * *', got '{val}'"
    await check("点预设后 cron 填入 '*/5 * * * *'", page, _cron_filled)

    # 非法 cron
    await page.locator("textarea").first.fill("not valid cron")
    await page.wait_for_timeout(300)

    async def _cron_error(p):
        # 错误消息文案可能不同（5字段、数值越界、JSON...），匹配红色提示元素
        await expect(p.locator('p.text-\\[\\#d45656\\]').first).to_be_visible()
    await check("非法 cron 显示错误提示", page, _cron_error)
    await screenshot(page, "phase3-cron-error")

    async def _cron_red(p):
        has_red = await p.locator("textarea").first.evaluate(
            "el => el.className.includes('red') || el.className.includes('d45656') || el.className.includes('error')"
        )
        assert has_red, "no red/error class"
    await check("非法 cron 时 textarea 红框", page, _cron_red)

    # 合法 cron
    await page.locator("textarea").first.fill("0 9 * * *")
    await page.wait_for_timeout(400)

    async def _cron_preview(p):
        await expect(p.locator('text=下次触发').first).to_be_visible()
    await check("合法 cron 显示『下次触发』预览", page, _cron_preview)
    await screenshot(page, "phase3-cron-good")

    async def _btn_disabled(p):
        dis = await p.locator('button:has-text("创建任务")').first.evaluate("el => el.disabled")
        assert dis is True, f"disabled={dis}, expected True"
    await check("名称为空时创建按钮 disabled", page, _btn_disabled)

    # ─── Phase 4/5/6: 提交 + 删除 + 展开（仅当有 skill）──
    skill_options = await page.locator("select").first.evaluate(
        "el => [...el.options].map(o => o.value).filter(v => v)"
    )
    has_skill = bool(skill_options)

    if not has_skill:
        print("\n-- Phase 4-6 skipped (no skills installed) --")
        await page.locator('button:has-text("取消")').first.click()
        return

    skill_name = skill_options[0]
    print(f"\n-- Phase 4: Submit + list (using skill: {skill_name}) --")

    await page.locator('select').first.select_option(skill_name)
    await page.locator('textarea').first.fill("0 9 * * *")
    await page.locator('input[placeholder*="36氪"]').fill("E2E测试任务")
    await page.wait_for_timeout(300)

    async def _btn_enabled(p):
        dis = await p.locator('button:has-text("创建任务")').first.evaluate("el => el.disabled")
        assert dis is False, f"disabled={dis}, expected False"
    await check("填好后创建按钮 enabled", page, _btn_enabled)

    await page.locator('button:has-text("创建任务")').first.click()
    await page.wait_for_timeout(1500)

    async def _task_in_list(p):
        await expect(p.locator('text=E2E测试任务').first).to_be_visible()
    await check("提交后新任务出现在列表", page, _task_in_list)
    await screenshot(page, "phase4-task-created")

    print("\n-- Phase 5: Delete confirmation --")

    await page.locator('button[title="删除"]').first.click()
    await page.wait_for_timeout(400)

    async def _confirm(p):
        await expect(p.locator('text=删除任务？').first).to_be_visible()
    await check("点删除出现确认弹窗", page, _confirm)
    await screenshot(page, "phase5-confirm")

    # 取消
    await page.locator('button:has-text("取消")').last.click()
    await page.wait_for_timeout(400)

    async def _cancel_keep(p):
        await expect(p.locator('text=删除任务？')).to_have_count(0)
        await expect(p.locator('text=E2E测试任务').first).to_be_visible()
    await check("取消后任务仍在列表", page, _cancel_keep)

    print("\n-- Phase 6: Card expand --")

    # 点展开任务
    await page.locator('span:has-text("E2E测试任务")').first.click()
    await page.wait_for_timeout(400)

    async def _expanded(p):
        await expect(p.locator('pre:has-text("0 9 * * *")').first).to_be_visible()
    await check("展开任务看到 cron 详情", page, _expanded)
    await screenshot(page, "phase6-card-expanded")


async def main():
    print("=== SchedulerPanel E2E Test ===\n")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=True,
        )
        page = await context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(f"PageError: {err}"))

        await page.goto(URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        try:
            await run_all_tests(page)
        except Exception as e:
            print(f"\nFATAL: {e}")

        print("\n======================================")
        print(f"Result: {PASS} passed, {FAIL} failed")
        if FAIL > 0:
            print("\nFails:")
            for r in RESULTS:
                if r["status"] == "FAIL":
                    print(f"  ✗ {r['name']}: {r['err'][:200]}")
        if console_errors:
            print(f"\n{len(console_errors)} console errors:")
            for e in console_errors[:5]:
                print(f"  - {e[:200]}")
        print("======================================")

        await browser.close()
        return PASS, FAIL


if __name__ == "__main__":
    p, f = asyncio.run(main())
    sys.exit(0 if f == 0 else 1)
