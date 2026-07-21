"""精确定位 /api/mcp/servers 崩溃 — 带完整 traceback"""
import sys, os, time, json, http.client, socket, threading, traceback
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

HOST = "127.0.0.1"
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.bind((HOST, 0))
PORT = sock.getsockname()[1]
sock.close()

from src.web.server import _run_server
errors = []

def run():
    try:
        _run_server(HOST, PORT)
    except Exception as e:
        errors.append(traceback.format_exc())

t = threading.Thread(target=run, daemon=True)
t.start()

for i in range(40):
    if errors:
        print(f"SERVER ERROR: {errors[0]}")
        sys.exit(1)
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=0.5)
        conn.request("GET", "/api/status")
        conn.getresponse().read()
        conn.close()
        break
    except:
        time.sleep(0.25)
else:
    print("Server not started")
    sys.exit(1)

print(f"Server ready on {PORT}")

# 用 _start_bot_in_thread 启动 bot
import src.web.server as sws

def safe_start_bot():
    try:
        from src.bot import Bot
        from src.config import load_config
        sws.update_status(running=False, error="")
        config = load_config()
        sws.update_status(wechat_backend=config.wechat_backend, model_name=config.ai_provider_model or "")
        bot = Bot(config)
        # 注册 MCP manager 到 server (正常 bot.run 会做, 这里手动做)
        try:
            from src.mcp.manager import MCPServerManager
            from src.web.server import register_mcp_status
            mgr = MCPServerManager()
            r = mgr.init_from_config(config_path="data/user_mcp.json")
            print(f"MCP init: {r}")
            if r["count"] > 0:
                register_mcp_status(mgr)
                print("MCP registered")
            else:
                print("MCP skipped (no config)")
                register_mcp_status(mgr)  # 强制注册，对齐实际启动
        except Exception as e:
            print(f"MCP init error: {e}")
            traceback.print_exc()
        bot.run()
    except Exception as e:
        print(f"BOT ERROR: {e}")
        traceback.print_exc()

bt = threading.Thread(target=safe_start_bot, daemon=True)
bt.start()
time.sleep(3)

# 等待 running = True
for i in range(30):
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=2)
        conn.request("GET", "/api/status")
        r = conn.getresponse()
        d = json.loads(r.read())
        conn.close()
        print(f"Status: running={d.get('running')} error='{d.get('error','')[:60]}'")
        if d.get("running"):
            break
    except Exception as e:
        print(f"Status error: {e}")
    time.sleep(1)
else:
    print("Bot not running, continuing anyway")

# 致命测试：GET /api/mcp/servers
print("\n=== CRITICAL: GET /api/mcp/servers ===")
try:
    conn = http.client.HTTPConnection(HOST, PORT, timeout=5)
    conn.request("GET", "/api/mcp/servers")
    r = conn.getresponse()
    d = json.loads(r.read())
    conn.close()
    print(f"SUCCESS: ok={d.get('ok')}, servers={len(d.get('servers',[]))}")
except Exception as e:
    print(f"CRASH: {type(e).__name__}: {e}")
    traceback.print_exc()
    if errors:
        print(f"Server thread error: {errors[-1]}")
    sys.exit(1)

print("\nMCP endpoint works. Test passed.")
sys.exit(0)
