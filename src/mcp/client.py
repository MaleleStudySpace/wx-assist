"""MCP transport 层 —— JSON-RPC 2.0 over stdio / HTTP。

两个子类: StdioClient (本地进程), HttpClient (远程 HTTP)。

全同步实现 (threading + queue)，适配 wx-assist 纯 sync 架构。
不依赖第三方 MCP SDK，纯 stdlib + requests。
"""

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────

MCP_PROTOCOL_VERSION = "2024-11-05"

# ── 抽象基类 ─────────────────────────────────────────────────────────────

class MCPClient:
    """MCP client 抽象基类。所有 transport 子类实现相同接口。"""

    def __init__(self, config: dict):
        self.config = config
        self.name = config["name"]
        self._timeout = config.get("timeout", 30)
        self._connected = False

    def initialize(self, timeout=None):
        """握手: 向 server 报身份。返回 server 的 capabilities。"""
        raise NotImplementedError

    def list_tools(self, timeout=None):
        """列工具。返回 list[dict]。"""
        raise NotImplementedError

    def call_tool(self, name, arguments, timeout=None):
        """调工具。返回 result dict。"""
        raise NotImplementedError

    def ping(self, timeout=None):
        """心跳检测。返回 bool。"""
        raise NotImplementedError

    def close(self):
        """关闭连接 / 终止子进程。"""
        raise NotImplementedError

    @property
    def connected(self):
        return self._connected

    def _send_request(self, method, params=None, timeout=None):
        """发送 JSON-RPC 请求，等待响应。子类实现。"""
        raise NotImplementedError


# ── Stdio transport ──────────────────────────────────────────────────────

class StdioClient(MCPClient):
    """本地 MCP server: 通过 subprocess 管理子进程，stdin/stdout JSON-RPC。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self._proc = None
        self._reader_thread = None
        self._pending = {}       # req_id → queue.SimpleQueue
        self._lock = threading.Lock()
        self._req_id = 0
        self._closed = False

    def initialize(self, timeout=None):
        """启动子进程 + 握手。"""
        timeout = timeout or 10
        self._spawn()
        resp = self._send_request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "wx-assist", "version": "1.2.0"},
        }, timeout=timeout)
        self._connected = True
        return resp.get("result", {})

    def _spawn(self):
        """启动子进程 (subprocess.Popen)。"""
        cmd = self.config["command"]
        args_list = self.config.get("args", [])
        extra_env = self.config.get("env", {})

        env = os.environ.copy()
        env.update(extra_env)
        # Windows 强制 UTF-8，绕开默认 codepage
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # 发送敏感变量 (WCDB key、数据库路径等)
        for sensitive in ("WCDB_KEY", "DATABASE_URL", "DATA_DIR", "PYTHONPATH"):
            env.pop(sensitive, None)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW  # 禁弹黑窗

        cwd = self.config.get("cwd", None)

        logger.info("[MCP] spawn: %s %s", cmd, args_list)
        self._proc = subprocess.Popen(
            [cmd] + args_list,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=creationflags,
            cwd=cwd,
            bufsize=0,
        )

        # 后台 reader 线程：持续读 stdout，分发到对应 pending queue
        self._reader_thread = threading.Thread(
            target=self._read_stdout, daemon=True, name="mcp-reader-{}".format(self.name)
        )
        self._reader_thread.start()

    def _read_stdout(self):
        """后台线程: 循环读子进程 stdout，按 id 分发到对应 pending 队列。"""
        while not self._closed:
            try:
                line = self._proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue  # 跳过空行
                resp = json.loads(line_str)
                req_id = resp.get("id")
                if req_id is not None:
                    q = self._pending.pop(req_id, None)
                    if q:
                        q.put(resp)
                elif resp.get("method") == "notifications/message":
                    # Server-sent notification (非标准 MCP 但容错)
                    pass
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                # 非 JSON 行（如 debug 日志写到 stdout）→ 跳过，不崩溃
                logger.warning("[MCP] %s reader 收到非 JSON 行: %s", self.name, e)
                continue
            except (ValueError, OSError, EOFError) as e:
                if not self._closed:
                    logger.warning("[MCP] %s reader error: %s", self.name, e)
                break

    def _next_id(self):
        self._req_id += 1
        return self._req_id

    def _send_request(self, method, params=None, timeout=None):
        """发送 JSON-RPC 请求并等待响应。串行化 (Lock) 防止请求-响应匹配错乱。"""
        if self._proc is None or self._proc.poll() is not None:
            raise ConnectionError("{}: 子进程已终止".format(self.name))

        timeout = timeout or self._timeout
        req_id = self._next_id()
        body = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            body["params"] = params

        q = queue.SimpleQueue()
        with self._lock:
            self._pending[req_id] = q
            line = json.dumps(body, ensure_ascii=False) + "\n"
            self._proc.stdin.write(line.encode("utf-8"))
            self._proc.stdin.flush()

        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            self._pending.pop(req_id, None)
            raise TimeoutError("{}: {} 调用超时 ({}s)".format(self.name, method, timeout))

        if "error" in resp and resp["error"] is not None:
            err = resp["error"]
            raise RuntimeError("{}: {} 错误: {} {}".format(
                self.name, method, err.get("code", ""), err.get("message", "")))

        return resp

    def list_tools(self, timeout=None):
        resp = self._send_request("tools/list", timeout=timeout)
        return resp.get("result", {}).get("tools", [])

    def call_tool(self, name, arguments, timeout=None):
        resp = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        }, timeout=timeout)
        return resp.get("result", {})

    def ping(self, timeout=None):
        timeout = timeout or 5
        try:
            self._send_request("ping", timeout=timeout)
            return True
        except (TimeoutError, ConnectionError, RuntimeError):
            return False

    def close(self):
        """终止子进程。"""
        self._closed = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=3)
            except Exception:
                pass
        self._connected = False
        logger.info("[MCP] %s: 已关闭", self.name)


# ── HTTP transport ───────────────────────────────────────────────────────

class HttpClient(MCPClient):
    """远程 MCP server: 通过 HTTP POST 发 JSON-RPC。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self._session = None

    def initialize(self, timeout=None):
        timeout = timeout or 10
        resp = self._send_request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "wx-assist", "version": "1.2.0"},
        }, timeout=timeout)
        self._connected = True
        return resp.get("result", {})

    def _send_request(self, method, params=None, timeout=None):
        timeout = timeout or self._timeout
        url = self.config["url"]
        headers = self.config.get("headers", {})
        headers.setdefault("Content-Type", "application/json")

        body = {"jsonrpc": "2.0", "id": self._next_id(), "method": method}
        if params is not None:
            body["params"] = params

        # requests 延迟导入（项目已有 requests）
        import requests

        try:
            r = requests.post(url, json=body, headers=headers, timeout=timeout)
            r.raise_for_status()
            resp = r.json()
        except requests.Timeout:
            raise TimeoutError("{}: {} HTTP 超时 ({}s)".format(self.name, method, timeout))
        except requests.ConnectionError as e:
            raise ConnectionError("{}: {} 连接失败: {}".format(self.name, method, e))

        if "error" in resp and resp["error"] is not None:
            err = resp["error"]
            raise RuntimeError("{}: {} 错误: {} {}".format(
                self.name, method, err.get("code", ""), err.get("message", "")))

        return resp

    def _next_id(self):
        # 简单自增，每次 new 一个 client 重置
        if not hasattr(self, "_req_id"):
            self._req_id = 0
        self._req_id += 1
        return self._req_id

    def list_tools(self, timeout=None):
        resp = self._send_request("tools/list", timeout=timeout)
        return resp.get("result", {}).get("tools", [])

    def call_tool(self, name, arguments, timeout=None):
        resp = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        }, timeout=timeout)
        return resp.get("result", {})

    def ping(self, timeout=None):
        timeout = timeout or 5
        try:
            self._send_request("ping", timeout=timeout)
            return True
        except (TimeoutError, ConnectionError, RuntimeError):
            return False

    def close(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        self._connected = False
        logger.info("[MCP] %s: HTTP 已关闭", self.name)


# ── Factory ──────────────────────────────────────────────────────────────

def create_client(config: dict) -> MCPClient:
    """根据配置创建对应 transport 的 MCP client。"""
    transport = config.get("transport", "stdio")
    if transport == "stdio":
        return StdioClient(config)
    elif transport == "http":
        return HttpClient(config)
    else:
        raise ValueError("不支持的 transport: {}".format(transport))
