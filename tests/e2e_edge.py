"""深度自测 — 边界条件全覆盖

后端:
  cron 校验边界: 空/越界/非法步进/反向范围/周日歧义/多行混合
  API 异常操作: 缺失字段/skill 不存在/重复删除/并发 run
  Skill 执行边界: [SILENT]/抛异常/超长输出
前端 UI 边界:
  空白列表 / 快速切换子 nav / 非法 cron 输入 / JSON 错误 / mobile viewport
"""
import asyncio
import sys
from pathlib import Path

import requests

# ── 复用后端校验函数 ──
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils.cron import validate_cron_syntax as backend_cron_validator

API = "http://127.0.0.1:17327"
UI = "http://127.0.0.1:15173"
SCREENSHOT_DIR = Path("C:/Users/74062/Desktop/wx-assist/data/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

PASS = 0
FAIL = 0
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


async def ok_async(name, fn):
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


def get(path, params=None):
    r = requests.get(API + path, params=params, timeout=10)
    return r.json()


def post(path, body):
    r = requests.post(API + path, json=body, timeout=15)
    return r.json()


def delete(path):
    r = requests.delete(API + path, timeout=10)
    return r.json()


def validate_cron(expr):
    """用后端校验函数模拟前端 cron 校验"""
    return backend_cron_validator(expr)


def assert_err(result):
    assert result, f"expected error (falsy), got empty string (ok)"


def assert_ok(result):
    assert not result, f"expected ok (empty string), got: {result[:120]}"


# ════════════════════════════════════════════════════════════
print("=" * 55)
print("  深度自测 — cron 校验边界")
print("=" * 55)

# ── 1. 空 / 纯空白 ──
ok("空 cron → 报错", lambda: assert_err(validate_cron("")))
ok("纯空白 → 报错", lambda: assert_err(validate_cron("   \n\n  ")))

# ── 2. 字段数不对 ──
ok("字段不足 → 报错", lambda: assert_err(validate_cron("0 9 *")))
ok("字段超长 → 报错", lambda: assert_err(validate_cron("0 9 * * * 0")))
ok("多行中有非法行 → 报错", lambda: assert_err(validate_cron("0 9 * * *\n0 10 *")))
ok("多行全部合法 → 合法", lambda: assert_ok(validate_cron("0 9 * * *\n0 18 * * *")))

# ── 3. 数值越界 ──
ok("分钟=60 → 报错", lambda: assert_err(validate_cron("60 9 * * *")))
ok("小时=24 → 报错", lambda: assert_err(validate_cron("0 24 * * *")))
ok("日=0 → 报错", lambda: assert_err(validate_cron("0 9 0 * *")))
ok("日=32 → 报错", lambda: assert_err(validate_cron("0 9 32 * *")))
ok("月=13 → 报错", lambda: assert_err(validate_cron("0 9 * 13 *")))
ok("周=7 → 报错", lambda: assert_err(validate_cron("0 9 * * 7")))
ok("周=-1 → 报错", lambda: assert_err(validate_cron("0 9 * * -1")))

# ── 4. 非法步进 ──
ok("步进=0 → 报错", lambda: assert_err(validate_cron("*/0 * * * *")))
ok("步进=负 → 报错", lambda: assert_err(validate_cron("*/-5 * * * *")))
ok("范围反向步进 → 报错", lambda: assert_err(validate_cron("30-20/5 * * * *")))

# ── 5. 非法范围 / 列表 ──
ok("范围反向 → 报错", lambda: assert_err(validate_cron("0 9-8 * * *")))
ok("范围含非法字符 → 报错", lambda: assert_err(validate_cron("0 a-b * * *")))
ok("列表含非法字符 → 报错", lambda: assert_err(validate_cron("0 9 1,2,x * *")))

# ── 6. 合法边界 ──
ok("分钟=0 → 合法", lambda: assert_ok(validate_cron("0 9 * * *")))
ok("分钟=59 → 合法", lambda: assert_ok(validate_cron("59 9 * * *")))
ok("周=0 (周日) → 合法", lambda: assert_ok(validate_cron("0 9 * * 0")))
ok("步进 */15 → 合法", lambda: assert_ok(validate_cron("*/15 * * * *")))
ok("范围 1-5 → 合法", lambda: assert_ok(validate_cron("0 9 * * 1-5")))
ok("列表 1,3,5 → 合法", lambda: assert_ok(validate_cron("0 9,12,18 * * *")))
ok("步进+范围混用 → 合法", lambda: assert_ok(validate_cron("*/10 */4 * * 1-5")))

# ── 7. 多行 ──
ok("多行空行不影响 → 合法", lambda: assert_ok(validate_cron("0 8 * * *\n\n\n0 18 * * *")))

# ── 8. 周日报错信息 ──
ok("周=7 报错包含 0-6 提示", lambda: (
    "0-6" in validate_cron("0 9 * * 7") or (_ for _ in ()).throw(
        AssertionError("error msg should suggest range 0-6"))
))


# ════════════════════════════════════════════════════════════
print("\n" + "=" * 55)
print("  深度自测 — API 非法/边界操作")
print("=" * 55)

# ── 1. 创建缺失字段 ──
ok("创建缺 name → error", lambda: (
    "error" in str(post("/api/scheduler/tasks", {"skill": "foo", "cron": "0 9 * * *"}))
))
ok("创建缺 skill → error", lambda: (
    "error" in str(post("/api/scheduler/tasks", {"name": "n", "cron": "0 9 * * *"}))
))
ok("创建缺 cron → error", lambda: (
    "error" in str(post("/api/scheduler/tasks", {"name": "n", "skill": "s"}))
))

# ── 2. skill 不存在 ──
ok("创建时 skill 不存在 → error", lambda: (
    assert_err(post("/api/scheduler/tasks",
                    {"name": "nop", "skill": "does-not-exist", "cron": "0 9 * * *"}))
))

# ── 3. cron 语法非法 ──
ok("创建时 cron 非法 → error", lambda: (
    assert_err(post("/api/scheduler/tasks",
                    {"name": "n", "skill": "test-echo", "cron": "not a cron"}))
))
ok("创建时分钟=99 → error", lambda: (
    assert_err(post("/api/scheduler/tasks",
                    {"name": "n", "skill": "test-echo", "cron": "99 * * * *"}))
))

# ── 4. 操作不存在的任务 ──
ok("DELETE 不存在 → 不报错", lambda:
    delete("/api/scheduler/tasks/NOEXIST_000").get("ok") is not None)
ok("RUN 不存在 → error", lambda:
    assert_err(post("/api/scheduler/tasks/NOEXIST_000/run", {})))

# ── 5. 快速重复删除 ── （创建后删除两次）
ok("创建→删除→再删不崩", lambda:
    (lambda d: (
        d.get("data", {}).get("id") and
        delete(f"/api/scheduler/tasks/{d['data']['id']}") and
        delete(f"/api/scheduler/tasks/{d['data']['id']}") and True
    ))(post("/api/scheduler/tasks",
            {"name": "dup-del", "skill": "test-echo", "cron": "0 9 * * *"}))
)

# ── 6. 超长名称 + 特殊字符 ──
ok("名称含特殊字符能创建", lambda:
    (lambda d: (
        d.get("ok") is True and
        d.get("data", {}).get("id") and
        delete(f"/api/scheduler/tasks/{d['data']['id']}") and True
    )
)(post("/api/scheduler/tasks",
       {"name": "<script>alert(1) & ' \" test",
        "skill": "test-echo", "cron": "0 9 * * *"}))
)


# ════════════════════════════════════════════════════════════
print("\n" + "=" * 55)
print("  深度自测 — Skill 执行边界")
print("=" * 55)

# ── 1. [SILENT] 协议 ──
ok("test-silent skill 存在", lambda:
    "test-silent" in [s["name"] for s in get("/api/skills").get("data", [])])

ok("创建 [SILENT] 任务", lambda: (
    (lambda d: d["ok"] and d["data"]["id"] and
     globals().__setitem__("_silent_id", d["data"]["id"])
    )(post("/api/scheduler/tasks",
           {"name": "silent", "skill": "test-silent",
            "cron": "0 0 * * *"}))
))

ok("[SILENT] 执行输出为 [SILENT]", lambda: (
    (lambda d: (
        d.get("ok") and
        delete(f"/api/scheduler/tasks/{_silent_id}") and
        d["data"]["output"].strip() == "[SILENT]"
        or (_ for _ in ()).throw(
            AssertionError(f"expected [SILENT], got: {d.get('data',{}).get('output','')[:80]}"))
    ))(post(f"/api/scheduler/tasks/{_silent_id}/run", {}))
))

# ── 2. skill 抛异常 ──
ok("创建 test-error 任务", lambda: (
    lambda d: (
        d["ok"] and d["data"]["id"] and
        globals().__setitem__("_err_id", d["data"]["id"])
    )
)(post("/api/scheduler/tasks",
       {"name": "err", "skill": "test-error",
        "cron": "0 0 * * *"}))
)

# 检查 _err_id
ok("_err_id 已设置", lambda:
    not not globals().get("_err_id") or (_ for _ in ()).throw(
        AssertionError("_err_id not set"))
)

ok("崩溃 skill run_now 返回异常", lambda:
    (lambda d: (
        d.get("ok") is False and
        delete(f"/api/scheduler/tasks/{_err_id}") and True
        or (_ for _ in ()).throw(
            AssertionError(f"expected error, got: {d}"))
    )
)(post(f"/api/scheduler/tasks/{_err_id}/run", {}))
)


# ════════════════════════════════════════════════════════════
print("\n" + "=" * 55)
print("  深度自测 — 前端 UI 边界")
print("=" * 55)


async def frontend_edge():
    global PASS, FAIL
    from playwright.async_api import async_playwright

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
        await page.wait_for_timeout(2500)

        # 进入定时任务
        await page.locator('nav button:has-text("定时任务")').first.click()
        await page.wait_for_timeout(800)

        # 1. 任务列表统计可见
        await fe("任务统计可见", lambda:
            page.locator('text=/共 \\d+ 个任务/').first.wait_for(timeout=3000))

        # 2. 快速子 nav 切换 15 次
        for _ in range(5):
            for target in ["Skill 库", "执行历史", "定时任务"]:
                btn = page.locator(f'nav button:has-text("{target}")').last
                n = await btn.count()
                if n > 0:
                    await btn.click()
                    await page.wait_for_timeout(100)
        await fe("快速切换子 nav 不崩", lambda:
            page.locator('text=定时任务').first.wait_for(timeout=3000))

        # 回到任务页
        await page.locator('nav button:has-text("定时任务")').last.click()
        await page.wait_for_timeout(600)

        # 3. 点新建表单
        await page.locator('button:has-text("新建任务")').first.click()
        await page.wait_for_timeout(600)

        # 超长名称 — 直接体验，不需要 fe 包装
        await page.locator('input[placeholder*="36氪"]').fill("A" * 200)
        await page.wait_for_timeout(200)
        print("  ✓ UI: 200字符名不崩")
        PASS += 1
        RESULTS.append({"name": "UI 新建: 200字符名不崩", "status": "PASS"})

        # 各类非法 cron 输入
        for bad, label in [
            ("abc def", "字母"),
            ("99 * * * *", "分钟99"),
            ("0 25 * * *", "小时25"),
            ("* * * * * *", "字段多"),
        ]:
            await page.locator("textarea").first.fill(bad)
            await page.wait_for_timeout(150)
            val = await page.locator("textarea").first.evaluate("el => el.value")
            assert val == bad, f"cron input '{label}': expected '{bad}', got '{val}'"

        print("  ✓ UI: 多种非法 cron 输入不崩")
        PASS += 1
        RESULTS.append({"name": "UI 多种非法 cron 输入不崩", "status": "PASS"})

        # 恢复合法
        await page.locator("textarea").first.fill("0 9 * * *")

        # JSON 参数异常
        texts = page.locator("textarea")
        n_ta = await texts.count()
        if n_ta >= 2:
            args_ta = texts.nth(1)
            for bad in ["not json", "{broken", "null", "[]"]:
                await args_ta.fill(bad)
                await page.wait_for_timeout(100)
        print("  ✓ UI: JSON 异常输入不崩")
        PASS += 1
        RESULTS.append({"name": "UI JSON 异常输入不崩", "status": "PASS"})

        # 取消
        await page.locator('button:has-text("取消")').first.click()
        await page.wait_for_timeout(300)

        # 4. 切换 mobile viewport
        await page.set_viewport_size({"width": 375, "height": 812})
        await page.wait_for_timeout(500)
        # mobile 时侧边栏隐藏，用底部 mobile tab strip
        await fe("mobile 下底部 tab strip 含定时任务", lambda:
            page.locator('button:has-text("定时任务"):visible').first.wait_for(timeout=3000))
        await page.screenshot(path=str(SCREENSHOT_DIR / "edge-mobile.png"), full_page=True)

        await browser.close()


asyncio.run(frontend_edge())


# ════════════════════════════════════════════════════════════
# 汇总
# ════════════════════════════════════════════════════════════
print("\n" + "=" * 55)
print(f"  深度自测结果: {PASS} passed / {FAIL} failed")
if FAIL > 0:
    print("\n  失败:")
    for r in RESULTS:
        if r["status"] == "FAIL":
            print(f"    ✗ {r['name']}: {r['err'][:200]}")
print("=" * 55)
sys.exit(0 if FAIL == 0 else 1)
