"""端到端测试 MCP server 是否能正常使用。
独立脚本，不依赖 wx-assist bot，直接调 MCP client 调工具。"""
import sys, os, json, time

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.mcp.client import create_client

results = {"pass": 0, "fail": 0, "detail": []}
def ok(msg, data=None):
    results["pass"] += 1
    r = {"status": "PASS", "msg": msg}
    if data:
        r["data"] = str(data)[:200]
    results["detail"].append(r)
    print(f"  [PASS] {msg}")

def fail(msg, err=None):
    results["fail"] += 1
    r = {"status": "FAIL", "msg": msg}
    if err:
        r["error"] = str(err)[:300]
    results["detail"].append(r)
    print(f"  [FAIL] {msg}: {err}")

# ── 1. filesystem MCP ──────────────────────────────────────────────
print("\n" + "=" * 50)
print(" 1. filesystem MCP")
print("=" * 50)
fs_config = {
    "name": "filesystem",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\74062\\Desktop"],
}
client = None
try:
    client = create_client(fs_config)
    ok("create_client 成功")

    # 初始化
    t0 = time.time()
    caps = client.initialize(timeout=60)
    t = time.time() - t0
    ok("initialize 握手成功 ({:.1f}s)".format(t))

    # tools/list
    tools = client.list_tools(timeout=15)
    ok("list_tools: {} 个工具".format(len(tools)))

    # call_tool: list_allowed_directories
    result = client.call_tool("list_allowed_directories", {}, timeout=10)
    content = result.get("content", [])
    text = "".join(c.get("text", "") for c in content)
    ok("list_allowed_directories 返回", text[:200])

    # call_tool: read_text_file
    result = client.call_tool("read_text_file", {
        "path": "C:\\Users\\74062\\Desktop\\wx-assist\\README.md"
    }, timeout=10)
    content = result.get("content", [])
    text = "".join(c.get("text", "") for c in content)
    ok("read_text_file README.md 返回 {} 字符".format(len(text)))

except Exception as e:
    import traceback
    fail("filesystem 测试异常", "{}\n{}".format(e, traceback.format_exc()[-500:]))
finally:
    if client:
        client.close()

# ── 2. TokenHub MCP ────────────────────────────────────────────────
print("\n" + "=" * 50)
print(" 2. TokenHub MCP (111)")
print("=" * 50)

# 从配置中获取 API Key — 直接读 user_mcp.json
tokenhub_key = None
try:
    with open("data/user_mcp.json", encoding="utf-8") as f:
        d = json.load(f)
    for s in d.get("servers", []):
        if s["name"] == "111":
            tokenhub_key = s.get("env", {}).get("TOKENHUB_API_KEY")
except Exception:
    pass

if not tokenhub_key:
    print("  [SKIP] 找不到 TOKENHUB_API_KEY，跳过 TokenHub MCP 测试")
else:
    th_config = {
        "name": "111",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@tokenhub-cash/mcp"],
        "env": {
            "TOKENHUB_BASE_URL": "https://api.tokenhub.market",
            "TOKENHUB_API_KEY": tokenhub_key,
            "TOKENHUB_MCP_ENABLED_TOOLS": "search,vision_analyze",
        },
    }
    client2 = None
    try:
        client2 = create_client(th_config)
        ok("create_client 成功 (TokenHub)")

        t0 = time.time()
        caps = client2.initialize(timeout=60)
        t = time.time() - t0
        ok("initialize 握手成功 ({:.1f}s)".format(t))

        tools = client2.list_tools(timeout=15)
        ok("list_tools: {} 个工具".format(len(tools)))

        # call_tool: search — 实际搜一下
        result = client2.call_tool("search", {"query": "今天天气", "count": 3}, timeout=30)
        content = result.get("content", [])
        text = "".join(c.get("text", "") for c in content)
        ok("search 返回结果", text[:300])

    except Exception as e:
        import traceback
        fail("TokenHub MCP 测试异常", "{}\n{}".format(e, traceback.format_exc()[-500:]))
    finally:
        if client2:
            client2.close()

# ── 汇总 ────────────────────────────────────────────────────────────
print("\n" + "=" * 50)
print(" 汇总")
print("=" * 50)
print("  通过: {}  失败: {}".format(results["pass"], results["fail"]))
for r in results["detail"]:
    tag = "PASS" if r["status"] == "PASS" else "FAIL"
    print("  [{}] {}".format(tag, r["msg"]))
    if "data" in r:
        print("      data: {}".format(r["data"]))
    if "error" in r:
        print("      error: {}".format(r["error"]))

sys.exit(0 if results["fail"] == 0 else 1)
