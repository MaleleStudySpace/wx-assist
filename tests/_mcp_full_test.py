"""MCP 全链路深度测试。

测试场景:
  1. StdioClient — initialize / list_tools / call_tool / ping / timeout
  2. MCPServerManager — init / add / remove / shutdown / 心跳降级
  3. HTTP API — POST/GET/DELETE/restart 端点 + 状态字段
  4. 错误路径 — 工具不存在 / 连接超时 / 子进程崩溃

用法:
  cd wx-assist && PYTHONPATH=. D:\\Python313\\python.exe -B tests/_mcp_full_test.py
"""

import sys, os, json, time, threading, subprocess, queue
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

TEST_SERVER = r"C:\Users\74062\AppData\Local\Temp\mcp_test\test_mcp_server.py"
SLOW_SERVER = r"C:\Users\74062\AppData\Local\Temp\mcp_test\slow_server.py"

passed = 0
failed = 0


def ok(name):
    global passed; passed += 1
    print(f"  [OK] {name}")


def fail(name, detail):
    global failed; failed += 1
    print(f"  [FAIL] {name}: {detail}")


def section(n, title):
    print(f"\n{'='*60}")
    print(f"  [{n}] {title}")
    print(f"{'='*60}")


# ═══════════════════════════════════════════════════════
#  1. StdioClient 基本协议
# ═══════════════════════════════════════════════════════

section("1", "StdioClient 协议测试")

from src.mcp.client import StdioClient, HttpClient, create_client
from src.mcp.manager import MCPServerManager
from src.mcp.config_schema import validate_config
from src.mcp.tool_registry import MCPToolRegistry, ProxyRegistry

# 1.1 StdioClient.initialize()
cfg = {"name": "test1", "transport": "stdio", "command": sys.executable, "args": [TEST_SERVER]}
client = StdioClient(cfg)
try:
    caps = client.initialize()
    assert caps.get("serverInfo", {}).get("name") == "test-mcp", "serverInfo.name 不对"
    ok("1.1 initialize 握手成功")
except Exception as e:
    fail("1.1 initialize 握手", e)

# 1.2 list_tools()
try:
    tools = client.list_tools()
    assert len(tools) == 1, f"应有 1 个工具，实际 {len(tools)}"
    assert tools[0]["name"] == "echo"
    ok("1.2 list_tools 返回正确")
except Exception as e:
    fail("1.2 list_tools", e)

# 1.3 call_tool()
try:
    result = client.call_tool("echo", {"text": "Hello MCP"})
    content = result.get("content", [])
    texts = [c["text"] for c in content if c.get("type") == "text"]
    assert "Hello MCP" in texts[0], f"回显内容不对: {texts}"
    ok("1.3 call_tool echo 正确")
except Exception as e:
    fail("1.3 call_tool echo", e)

# 1.4 ping()
try:
    assert client.ping() == True, "ping 返回 False"
    ok("1.4 ping 正常")
except Exception as e:
    fail("1.4 ping", e)

# 1.5 调用不存在的工具
try:
    client.call_tool("nonexistent", {})
    fail("1.5 不存在的工具应抛异常", "未抛异常")
except RuntimeError as e:
    ok("1.5 不存在的工具抛 RuntimeError")
except Exception as e:
    fail("1.5 不存在的工具预期 RuntimeError", f"实际抛 {type(e).__name__}: {e}")

# 1.6 close()
try:
    client.close()
    ok("1.6 close 正常")
except Exception as e:
    fail("1.6 close", e)


# ═══════════════════════════════════════════════════════
#  2. HttpClient (模拟远程)
# ═══════════════════════════════════════════════════════

section("2", "HttpClient 测试")

# 用同一份测试 server 通过 stdio 暴露, 不能用 HTTP client 连 stdio
# 所以这里只验证 HttpClient 不会崩溃，实际 HTTP 连通在 API 测
try:
    hc = HttpClient({"name": "http-test", "url": "http://127.0.0.1:1"})
    # 连不通的地址应抛 ConnectionError
    try:
        hc.initialize()
        fail("2.1 HttpClient 连不通应抛异常", "未抛异常")
    except ConnectionError:
        ok("2.1 HttpClient 连不通抛 ConnectionError")
    except OSError:
        ok("2.1 HttpClient 连不通抛 OSError (Windows)")
    except Exception as e:
        fail("2.1 HttpClient 连不通预期 ConnectionError", f"实际抛 {type(e).__name__}: {e}")
except Exception as e:
    fail("2.1 HttpClient 连不通", e)


# ═══════════════════════════════════════════════════════
#  3. MCPServerManager 生命周期
# ═══════════════════════════════════════════════════════

section("3", "MCPServerManager 生命周期")

# 3.1 init_from_config — 空
mgr = MCPServerManager()
r = mgr.init_from_config(configs=[])
if r["ok"] and r["count"] == 0:
    ok("3.1 空初始化通过")
else:
    fail("3.1 空初始化", f"结果: {r}")

# 3.2 init_from_config — 含测试 server
mgr2 = MCPServerManager()
r2 = mgr2.init_from_config(configs=[cfg])
if r2["count"] == 1:
    ok("3.2 初始化 1 个 server 成功")
else:
    fail("3.2 初始化 1 个 server", f"count={r2['count']} errors={r2['errors']}")

# 3.3 get_tool_table()
tools = mgr2.get_tool_table()
if len(tools) == 1 and tools[0]["server"] == "test1" and tools[0]["name"] == "echo":
    ok("3.3 get_tool_table 返回正确")
else:
    fail("3.3 get_tool_table", f"tools={tools}")

# 3.4 get_status()
status = mgr2.get_status()
if status.get("test1", {}).get("status") == "running":
    ok("3.4 get_status 返回 running")
else:
    fail("3.4 get_status", f"status={status}")

# 3.5 invoke()
try:
    result = mgr2.invoke("test1", "echo", {"text": "invoke test"})
    texts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
    if "invoke test" in texts[0]:
        ok("3.5 invoke 通过")
    else:
        fail("3.5 invoke", f"内容不对: {texts}")
except Exception as e:
    fail("3.5 invoke", e)

# 3.6 invoke 不存在的 server
try:
    mgr2.invoke("nonexistent", "echo", {})
    fail("3.6 invoke 不存在 server 应抛异常", "未抛异常")
except ValueError:
    ok("3.6 invoke 不存在 server 抛 ValueError")
except Exception as e:
    fail("3.6 invoke 不存在 server 预期 ValueError", f"实际抛 {type(e).__name__}: {e}")

# 3.7 shutdown_all
try:
    mgr2.shutdown_all()
    tools2 = mgr2.get_tool_table()
    if len(tools2) == 0:
        ok("3.7 shutdown_all 后工具表清空")
    else:
        fail("3.7 shutdown_all 后工具表", f"len={len(tools2)}")
except Exception as e:
    fail("3.7 shutdown_all", e)

# 3.8 shutdown_all 幂等
try:
    mgr2.shutdown_all()
    ok("3.8 shutdown_all 幂等")
except Exception as e:
    fail("3.8 shutdown_all 幂等", e)

# 3.9 add 运行时热加
mgr3 = MCPServerManager()
mgr3.init_from_config(configs=[])
try:
    mgr3.add(cfg)
    tools3 = mgr3.get_tool_table()
    if len(tools3) == 1:
        ok("3.9 运行时 add 通过")
    else:
        fail("3.9 运行时 add", f"tools={tools3}")
except Exception as e:
    fail("3.9 运行时 add", e)
mgr3.shutdown_all()

# 3.10 remove 运行时热删
try:
    mgr4 = MCPServerManager()
    mgr4.init_from_config(configs=[cfg])
    mgr4.remove("test1")
    tools4 = mgr4.get_tool_table()
    if len(tools4) == 0:
        ok("3.10 运行时 remove 通过")
    else:
        fail("3.10 运行时 remove", f"tools={tools4}")
except Exception as e:
    fail("3.10 运行时 remove", e)
mgr4.shutdown_all()


# ═══════════════════════════════════════════════════════
#  4. 降级机制
# ═══════════════════════════════════════════════════════

section("4", "降级机制测试")

# 4.1 配
timeout_cfg = {
    "name": "timeout-test",
    "transport": "stdio",
    "command": sys.executable,
    "args": [SLOW_SERVER],
    "timeout": 1,
}
mgr5 = MCPServerManager()
mgr5.init_from_config(configs=[timeout_cfg])

# 调用应抛 TimeoutError (server 延迟 3s, timeout 1s)
caught = 0
for i in range(3):
    try:
        mgr5.invoke("timeout-test", "slow_echo", {"text": "x"})
    except (TimeoutError, OSError, RuntimeError):
        caught += 1

if caught >= 1:
    ok(f"4.1 超时触发异常 (caught {caught}/3)")
else:
    fail("4.1 超时触发异常", f"caught {caught}/3")

# 第 4 次应抛 RuntimeError (已被降级)
try:
    mgr5.invoke("timeout-test", "slow_echo", {"text": "x"})
    fail("4.2 降级后应抛异常", "未抛异常")
except RuntimeError as e:
    if "降级" in str(e):
        ok("4.2 降级后抛 RuntimeError(降级)")
    else:
        fail("4.2 降级后抛 RuntimeError 但消息不对", str(e))
except Exception as e:
    fail("4.2 降级后预期 RuntimeError", f"实际抛 {type(e).__name__}: {e}")

mgr5.shutdown_all()


# ═══════════════════════════════════════════════════════
#  5. 子进程崩溃恢复
# ═══════════════════════════════════════════════════════

section("5", "子进程崩溃恢复")

# 用 subprocess 启动一个会崩溃的 server
crash_script = TEST_SERVER.replace("test_mcp_server.py", "crash_server.py")
with open(crash_script, "w", encoding="utf-8") as f:
    f.write("""import sys, json, time
# 发完 initialize 后睡 2 秒再退
def respond(id, result):
    msg = {"jsonrpc": "2.0", "id": id, "result": result}
    sys.stdout.buffer.write((json.dumps(msg) + "\\n").encode("utf-8"))
    sys.stdout.buffer.flush()
line = sys.stdin.buffer.readline()
req = json.loads(line.decode("utf-8").strip())
if req["method"] == "initialize":
    respond(req["id"], {
        "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
        "serverInfo": {"name": "crash-test", "version": "1.0"},
    })
# 然后等一秒再崩, 让它有时间读到 tools/list
import threading; threading.Thread(target=lambda: (__import__("time").sleep(1), __import__("os")._exit(1)), daemon=True).start()
# 继续读，等崩
while True:
    try:
        sys.stdin.buffer.readline()
    except:
        break
""")

crash_cfg = {"name": "crash-test", "transport": "stdio", "command": sys.executable, "args": [crash_script]}
mgr6 = MCPServerManager()
mgr6.init_from_config(configs=[crash_cfg])
time.sleep(1.5)

# 子进程已崩, call 应抛异常 (server 在 init 阶段崩, 未注册到 _clients)
try:
    mgr6.invoke("crash-test", "echo", {"text": "x"})
    fail("5.1 子进程崩溃后应抛异常", "未抛异常")
except (ConnectionError, RuntimeError, OSError, ValueError):
    ok("5.1 子进程崩溃后调用抛异常")
except Exception as e:
    fail("5.1 子进程崩溃后预期 ConnectionError/ValueError", f"实际抛 {type(e).__name__}: {e}")

mgr6.shutdown_all()


# ═══════════════════════════════════════════════════════
#  6. config_schema 校验
# ═══════════════════════════════════════════════════════

section("6", "配置校验")

# 6.1 合法 stdio
if validate_config([{"name": "s1", "transport": "stdio", "command": "x"}])["ok"]:
    ok("6.1 合法 stdio 通过")
else:
    fail("6.1 合法 stdio", "未通过")

# 6.2 合法 http
if validate_config([{"name": "h1", "transport": "http", "url": "https://x.com"}])["ok"]:
    ok("6.2 合法 http 通过")
else:
    fail("6.2 合法 http", "未通过")

# 6.3 短名拒绝
if not validate_config([{"name": "t", "transport": "stdio", "command": "x"}])["ok"]:
    ok("6.3 短名拒绝")
else:
    fail("6.3 短名拒绝", "应拒绝")

# 6.4 非法 transport
if not validate_config([{"name": "b1", "transport": "bluetooth", "command": "x"}])["ok"]:
    ok("6.4 非法 transport 拒绝")
else:
    fail("6.4 非法 transport", "应拒绝")

# 6.5 重名检测
r = validate_config([
    {"name": "dup", "transport": "stdio", "command": "x"},
    {"name": "dup", "transport": "stdio", "command": "y"},
])
if not r["ok"] and "dup" in r["errors"]:
    ok("6.5 重名检测")
else:
    fail("6.5 重名检测", str(r))


# ═══════════════════════════════════════════════════════
#  7. tool_registry dispatch
# ═══════════════════════════════════════════════════════

section("7", "MCPToolRegistry 分发")

class MockLocal:
    def __init__(self):
        self._tools = {}
    def get_all_schemas(self):
        return [{"function": {"name": "local_tool"}}]
    def execute(self, name, args):
        return "local_result"
    def get_descriptions(self):
        return "local tools"
    def register(self, name, **kw):
        self._tools[name] = kw
    def get(self, name):
        return self._tools.get(name)

local = MockLocal()
mgr7 = MCPServerManager()
mgr7.init_from_config(configs=[cfg])

wrapper = MCPToolRegistry(local, mgr7)
wrapper.refresh()

# 7.1 get_all_schemas 包含 MCP + 本地
schemas = wrapper.get_all_schemas()
schema_names = [s["function"]["name"] for s in schemas]
if "test1__echo" in schema_names and "local_tool" in schema_names:
    ok("7.1 get_all_schemas 合并本地+MCP")
else:
    fail("7.1 get_all_schemas", f"names={schema_names}")

# 7.2 execute 本地工具走 local
result_local = wrapper.execute("local_tool", {})
if result_local == "local_result":
    ok("7.2 execute 本地工具走 local")
else:
    fail("7.2 execute 本地工具", f"result={result_local}")

# 7.3 execute MCP 工具走 manager
try:
    result_mcp = wrapper.execute("test1__echo", {"text": "mcp dispatch"})
    if "mcp dispatch" in result_mcp:
        ok("7.3 execute MCP 工具走 manager")
    else:
        fail("7.3 execute MCP 工具", f"result={result_mcp}")
except Exception as e:
    fail("7.3 execute MCP 工具", e)

# 7.4 ProxyRegistry 透传
proxy = ProxyRegistry(local, wrapper)
if proxy.get_all_schemas() == wrapper.get_all_schemas():
    ok("7.4 ProxyRegistry get_all_schemas 透传")
else:
    fail("7.4 ProxyRegistry get_all_schemas", "不匹配")

if proxy.execute("local_tool", {}) == "local_result":
    ok("7.5 ProxyRegistry execute 本地工具")
else:
    fail("7.5 ProxyRegistry execute 本地工具")

# 在 shutdown 前测试 MCP 工具分发
if proxy.execute("test1__echo", {"text": "proxy"}) == wrapper.execute("test1__echo", {"text": "proxy"}):
    ok("7.6 ProxyRegistry execute MCP 工具")
else:
    fail("7.6 ProxyRegistry execute MCP 工具")

mgr7.shutdown_all()

# shutdown 后 proxy 的 __getattr__ 仍可透传
if proxy.get("test") == local.get("test") and proxy.register("test") == None:
    ok("7.7 ProxyRegistry __getattr__ 透传")
else:
    fail("7.7 ProxyRegistry __getattr__ 透传")


# ═══════════════════════════════════════════════════════
#  8. HTTP API 端点测试
# ═══════════════════════════════════════════════════════

section("8", "HTTP API 端点测试")

import urllib.request, urllib.error

# 启动 server
from src.web.server import start_web_server
start_web_server(port=17340)
time.sleep(2)
BASE = "http://127.0.0.1:17340"

def api(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(f"{BASE}{path}", data=data,
        method=method, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode()) if e.code != 501 else {"_error": f"HTTP {e.code}"}

# 8.1 GET /api/status — 确认含 mcp_servers
d = api("GET", "/api/status")
if "mcp_servers" in d:
    ok("8.1 /api/status 含 mcp_servers")
else:
    fail("8.1 /api/status 含 mcp_servers", f"keys={list(d.keys())}")

# 8.2 GET /api/mcp/servers — 空列表
d2 = api("GET", "/api/mcp/servers")
if d2.get("ok") and d2.get("servers") == []:
    ok("8.2 GET /api/mcp/servers 空列表")
else:
    fail("8.2 GET /api/mcp/servers", str(d2)[:200])

# 8.3 POST /api/mcp/servers — 新增
cfg_api = {"name": "api-test", "transport": "stdio", "command": sys.executable,
           "args": [TEST_SERVER], "description": "API 测试"}
d3 = api("POST", "/api/mcp/servers", cfg_api)
if d3.get("ok"):
    ok("8.3 POST /api/mcp/servers 新增成功")
else:
    fail("8.3 POST /api/mcp/servers", str(d3)[:200])
time.sleep(1)

# 8.4 GET /api/mcp/servers — 含新增的 server
d4 = api("GET", "/api/mcp/servers")
if d4.get("ok") and any(s["name"] == "api-test" for s in d4["servers"]):
    ok("8.4 GET 返回新增 server")
else:
    names = [s["name"] for s in d4.get("servers", [])]
    fail("8.4 GET 返回新增 server", f"names={names}")

# 8.5 POST /api/mcp/servers/<name>/restart
d5 = api("POST", "/api/mcp/servers/api-test/restart")
if d5.get("ok"):
    ok("8.5 restart API 通过")
else:
    fail("8.5 restart API", str(d5)[:200])

# 8.6 DELETE /api/mcp/servers/<name>
d6 = api("DELETE", "/api/mcp/servers/api-test")
if d6.get("ok"):
    ok("8.6 DELETE API 通过")
else:
    fail("8.6 DELETE API", str(d6)[:200])

# 8.7 验证删除后的列表
d7 = api("GET", "/api/mcp/servers")
if d7.get("ok") and not any(s["name"] == "api-test" for s in d7["servers"]):
    ok("8.7 删除后列表无残留")
else:
    fail("8.7 删除后列表", f"servers={[s['name'] for s in d7.get('servers', [])]}")


# ═══════════════════════════════════════════════════════
#  结果汇总
# ═══════════════════════════════════════════════════════

print(f"\n{'='*60}")
total = passed + failed
print(f"  结果: {passed}/{total} 通过, {failed} 失败")
if failed == 0:
    print("  [ALL PASS] 全部通过")
else:
    print(f"  [FAIL] {failed} 个失败")
print(f"{'='*60}")

sys.exit(0 if failed == 0 else 1)



