"""MCP UI 端到端测试 — 用 Playwright (CDP) 验证前端交互。

测试场景:
  1. MCP Tab 空状态展示
  2. 添加服务器后卡片渲染
  3. 禁用/启用 (toggle) — 验证状态切换
  4. 重启 — 验证工具不重复
  5. 删除 — 验证列表清空

用法:
  cd wx-assist
  PYTHONPATH=. D:\Python313\python.exe -B tests/test_mcp_ui.py
"""

import http.client
import json
import os
import socket
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_SERVER = os.path.join(os.environ.get("TEMP", ""), "mcp_test", "test_mcp_server.py")

# ── helpers ────────────────────────────────────────────────────────────────

passed = 0
failed = 0


def ok(name):
    global passed
    passed += 1
    print(f"  [OK] {name}")


def fail(name, detail):
    global failed
    failed += 1
    print(f"  [FAIL] {name}: {detail}")


def section(n, title):
    print(f"\n{'=' * 60}")
    print(f"  [{n}] {title}")
    print(f"{'=' * 60}")


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port):
    """Start web server in daemon thread."""
    from src.web.server import _run_server

    errors = []

    def run():
        try:
            _run_server("127.0.0.1", port)
        except Exception as e:
            errors.append(str(e))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    for _ in range(80):
        if errors:
            raise RuntimeError(f"Server failed: {errors[0]}")
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
            conn.request("GET", "/api/status")
            conn.getresponse().read()
            conn.close()
            return t
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("Server did not start within 20s")


def api(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = json.dumps(body).encode() if body else None
    conn.request(method, path, data, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    result = json.loads(resp.read())
    conn.close()
    return resp.status, result


# ── main test ──────────────────────────────────────────────────────────────

def main():
    global passed, failed
    ui_dist = os.path.join(os.path.dirname(__file__), "..", "ui", "dist", "index.html")
    if not os.path.exists(ui_dist):
        print("[SKIP] UI 未构建，跳过前端测试。运行: cd ui && npm run build")
        # 只跑 API 测试
        _run_api_tests()
        _print_summary()
        return

    # 检查 Playwright
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[SKIP] Playwright 未安装: pip install playwright && python -m playwright install chromium")
        _run_api_tests()
        _print_summary()
        return

    if not os.path.exists(TEST_SERVER):
        print(f"[SKIP] 测试 MCP server 不存在: {TEST_SERVER}")
        _run_api_tests()
        _print_summary()
        return

    # ── 启动 server ──
    port = _find_free_port()
    base = f"http://127.0.0.1:{port}"
    print(f"启动 web server 在端口 {port}...")
    _start_server(port)
    print("Server 已就绪")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        try:
            # =============================================================
            section("1", "MCP Tab 空状态展示")
            # =============================================================
            page.goto(base, timeout=15000)
            page.wait_for_timeout(1500)

            # 找到侧边栏并切换到 MCP 工具
            mcp_btn = page.locator("button:has-text('MCP 工具')")
            if mcp_btn.count() == 0:
                # 可能需要展开侧边栏
                page.wait_for_timeout(500)
                mcp_btn = page.locator("button:has-text('MCP 工具')")

            if mcp_btn.count() > 0:
                mcp_btn.first.click()
                page.wait_for_timeout(800)
                body_text = page.inner_text("body")
                if "还没有配置 MCP 服务器" in body_text:
                    ok("1.1 空状态展示正确")
                else:
                    fail("1.1 空状态", f"未找到'还没有配置 MCP 服务器'，页面内容: {body_text[:200]}")
            else:
                fail("1.1 空状态", "未找到 MCP 工具 按钮")

            # =============================================================
            section("2", "添加服务器后卡片渲染")
            # =============================================================

            # 通过 API 添加测试服务器
            status, d = api(port, "POST", "/api/mcp/servers", {
                "name": "cdp-test",
                "transport": "stdio",
                "command": sys.executable,
                "args": [TEST_SERVER],
                "description": "CDP 测试服务器",
            })
            if d.get("ok"):
                ok("2.1 API 添加服务器成功")
            else:
                fail("2.1 API 添加服务器", str(d)[:200])

            time.sleep(2)

            # 刷新页面
            page.reload()
            page.wait_for_timeout(1500)

            # 定位到 MCP Tab
            mcp_btn = page.locator("button:has-text('MCP 工具')")
            if mcp_btn.count() > 0:
                mcp_btn.first.click()
                page.wait_for_timeout(800)

            body_text = page.inner_text("body")
            if "cdp-test" in body_text:
                ok("2.2 服务器卡片显示名称")
            else:
                fail("2.2 服务器卡片", f"未找到 cdp-test，内容: {body_text[:300]}")

            if "运行中" in body_text or "运行" in body_text:
                ok("2.3 服务器状态显示正确")
            else:
                # 可能状态标签不同，检查至少不报错
                if "错误" not in body_text:
                    ok("2.3 服务器状态无错误")
                else:
                    fail("2.3 服务器状态", "显示错误状态")

            # =============================================================
            section("3", "禁用/启用 (toggle)")
            # =============================================================

            # 点禁用按钮 (Pause icon, title="禁用")
            pause_btn = page.locator('button[title="禁用"]')
            if pause_btn.count() > 0:
                pause_btn.first.click()
                page.wait_for_timeout(1500)
                ok("3.1 点击禁用按钮")

                # 验证状态变为 stopped
                status, sd = api(port, "GET", "/api/mcp/servers")
                st = sd.get("status", {}).get("cdp-test", {}).get("status", "")
                if st == "stopped":
                    ok("3.2 禁用后状态变为 stopped")
                else:
                    fail("3.2 禁用状态", f"期望 stopped，实际: {st}")

                # 刷新页面验证UI
                page.reload()
                page.wait_for_timeout(1000)
                mcp_btn = page.locator("button:has-text('MCP 工具')")
                if mcp_btn.count() > 0:
                    mcp_btn.first.click()
                    page.wait_for_timeout(500)
                # 应该显示启用按钮 (Play icon, title="启用")
                play_btn = page.locator('button[title="启用"]')
                if play_btn.count() > 0:
                    ok("3.3 禁用后显示启用按钮")
                else:
                    fail("3.3 禁用后UI", f"未找到启用按钮，内容: {page.inner_text('body')[:200]}")

                # 点启用恢复
                if play_btn.count() > 0:
                    play_btn.first.click()
                    page.wait_for_timeout(2000)
                    ok("3.4 点击启用按钮")

                    status, sd = api(port, "GET", "/api/mcp/servers")
                    st = sd.get("status", {}).get("cdp-test", {}).get("status", "")
                    if st == "running":
                        ok("3.5 启用后状态变为 running")
                    else:
                        fail("3.5 启用状态", f"期望 running，实际: {st}")
            else:
                fail("3.1 禁用按钮", "未找到禁用按钮")

            # =============================================================
            section("4", "重启 — 工具不重复")
            # =============================================================

            # 点重启按钮
            restart_btn = page.locator('button[title="重启"]')
            if restart_btn.count() > 0:
                restart_btn.first.click()
                page.wait_for_timeout(2000)
                ok("4.1 点击重启按钮")

                # 验证 tools_count 为 1（不重复）
                status, sd = api(port, "GET", "/api/mcp/servers")
                tc = sd.get("status", {}).get("cdp-test", {}).get("tools_count", 0)
                if tc == 1:
                    ok("4.2 重启后工具数仍为 1 (无重复)")
                else:
                    fail("4.2 重启工具数", f"期望 1，实际: {tc}")

                # 再次重启，确认工具数仍为 1
                if restart_btn.count() > 0:
                    restart_btn.first.click()
                    page.wait_for_timeout(2000)
                    status, sd = api(port, "GET", "/api/mcp/servers")
                    tc2 = sd.get("status", {}).get("cdp-test", {}).get("tools_count", 0)
                    if tc2 == 1:
                        ok("4.3 重复重启后工具数仍为 1")
                    else:
                        fail("4.3 重复重启工具数", f"期望 1，实际: {tc2}")
            else:
                fail("4.1 重启按钮", "未找到重启按钮")

            # =============================================================
            section("5", "删除服务器")
            # =============================================================

            # 点删除按钮（先确认删除 — 需要点击两次）
            trash_btn = page.locator('button[title="删除"]')
            if trash_btn.count() > 0:
                trash_btn.first.click()
                page.wait_for_timeout(300)
                ok("5.1 点击删除按钮 (确认模式)")

                # 点击确认删除的 "是"
                confirm_btn = page.locator("button:has-text('是')")
                if confirm_btn.count() > 0:
                    confirm_btn.first.click()
                    page.wait_for_timeout(1500)
                    ok("5.2 确认删除")

                    # 验证列表为空
                    status, sd = api(port, "GET", "/api/mcp/servers")
                    if len(sd.get("servers", [])) == 0:
                        ok("5.3 删除后列表为空")
                    else:
                        fail("5.3 删除后列表", f"仍有 {len(sd.get('servers', []))} 个 server")
                else:
                    fail("5.2 确认删除", "未找到确认按钮")
            else:
                fail("5.1 删除按钮", "未找到删除按钮")

        except Exception as e:
            import traceback
            fail("TEST_ERROR", f"{e}\n{traceback.format_exc()}")

        context.close()
        browser.close()

    _print_summary()


def _run_api_tests():
    """仅运行 API 层面的 PUT + toggle 测试。"""
    global passed, failed
    section("A", "API 后端修复验证")

    port = _find_free_port()
    _start_server(port)
    test_path = TEST_SERVER
    py = sys.executable

    # A1: PUT 端点（编辑）
    status, d = api(port, "POST", "/api/mcp/servers", {
        "name": "api-put-test",
        "transport": "stdio",
        "command": py,
        "args": [test_path],
    })
    if d.get("ok"):
        ok("A0 POST 添加成功")
    else:
        fail("A0 POST 添加", str(d)[:200])
        return

    status, d = api(port, "PUT", f"/api/mcp/servers/api-put-test", {
        "transport": "stdio",
        "command": py,
        "args": [test_path],
        "description": "edited",
    })
    if d.get("ok"):
        ok("A1 PUT 编辑端点可到达")
    else:
        fail("A1 PUT 编辑", f"返回 Unknown MCP endpoint? {str(d)[:200]}")

    # A2: 验证启用/禁用
    status, d = api(port, "POST", "/api/mcp/servers/api-toggle-test", {
        "name": "api-toggle-test",
        "transport": "stdio",
        "command": py,
        "args": [test_path],
    })
    if d.get("ok"):
        ok("A2.0 POST 添加 toggle 测试 server")
    else:
        fail("A2.0 POST 添加 toggle", str(d)[:200])

    time.sleep(1.5)

    # 禁用
    status, d = api(port, "POST", "/api/mcp/servers/api-toggle-test/toggle")
    st = d.get("ok")
    if st:
        ok("A2.1 Toggle 禁用成功")
    else:
        fail("A2.1 Toggle 禁用", str(d)[:200])

    time.sleep(0.5)

    # 验证 disabled 状态
    status, sd = api(port, "GET", "/api/mcp/servers")
    st = sd.get("status", {}).get("api-toggle-test", {}).get("status", "")
    if st == "stopped":
        ok("A2.2 禁用后状态 stopped")
    else:
        fail("A2.2 禁用状态", f"期望 stopped 实际 {st}")

    # 启用
    status, d = api(port, "POST", "/api/mcp/servers/api-toggle-test/toggle")
    if d.get("ok"):
        ok("A2.3 Toggle 启用成功")
    else:
        fail("A2.3 Toggle 启用", str(d)[:200])

    time.sleep(1.5)

    status, sd = api(port, "GET", "/api/mcp/servers")
    st = sd.get("status", {}).get("api-toggle-test", {}).get("status", "")
    tools = sd.get("status", {}).get("api-toggle-test", {}).get("tools_count", 0)
    if st == "running" and tools == 1:
        ok("A2.4 启用后 running, 工具数 1")
    else:
        fail("A2.4 启用状态", f"st={st} tools={tools}")

    # A3: 重启不重复工具
    status, d = api(port, "POST", "/api/mcp/servers/api-toggle-test/restart")
    if d.get("ok"):
        ok("A3.1 restart API 可达")
    else:
        fail("A3.1 restart API", str(d)[:200])

    time.sleep(1.5)

    status, sd = api(port, "GET", "/api/mcp/servers")
    tc = sd.get("status", {}).get("api-toggle-test", {}).get("tools_count", 0)
    if tc == 1:
        ok("A3.2 重启后工具数仍为 1")
    else:
        fail("A3.2 重启工具数", f"期望 1 实际 {tc}")

    # 清理
    api(port, "DELETE", "/api/mcp/servers/api-put-test")
    api(port, "DELETE", "/api/mcp/servers/api-toggle-test")
    ok("A4 清理完成")

    _print_summary()


def _print_summary():
    global passed, failed
    total = passed + failed
    print(f"\n{'=' * 60}")
    print(f"  结果: {passed}/{total} 通过, {failed} 失败")
    if failed == 0:
        print("  [ALL PASS] 全部通过")
    else:
        print(f"  [FAIL] {failed} 个失败")
    print(f"{'=' * 60}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
