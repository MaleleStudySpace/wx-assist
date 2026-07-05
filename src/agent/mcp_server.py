"""MCP Server — exposes ToolRegistry tools via HTTP.

Reads from the same ToolRegistry as AgentEngine, so tools only
need to be registered once in tools.py.

Usage:
    from src.agent.mcp_server import start_mcp_server
    start_mcp_server(tool_registry, port=17328)
"""

import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Optional

logger = logging.getLogger(__name__)

MCP_PORT = 17328


class _MCPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for MCP tool access."""

    # Shared registry reference (set by factory)
    registry = None

    def log_message(self, fmt, *args):
        logger.debug("[MCP] %s", fmt % args)

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """GET / → tool list (for discovery)."""
        if self.path != "/":
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

        if not self.registry:
            self._send_json(500, {"ok": False, "error": "Registry not initialized"})
            return

        tools = []
        for name in sorted(self.registry._tools.keys()):
            tool = self.registry.get(name)
            if not tool:
                continue
            tools.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "requires_confirm": tool.requires_confirm,
            })

        self._send_json(200, {"ok": True, "tools": tools, "count": len(tools)})

    def do_POST(self):
        """POST /call — execute a tool.

        Body: {"name": "get_status", "args": {}}
        """
        if self.path != "/call":
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

        if not self.registry:
            self._send_json(500, {"ok": False, "error": "Registry not initialized"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"ok": False, "error": f"Invalid JSON: {e}"})
            return

        name = body.get("name", "")
        args = body.get("args", {})

        if not name:
            self._send_json(400, {"ok": False, "error": "Missing 'name'"})
            return

        tool = self.registry.get(name)
        if not tool:
            self._send_json(404, {"ok": False, "error": f"Unknown tool: {name}"})
            return

        if tool.requires_confirm:
            self._send_json(403, {
                "ok": False, "error": "Tool requires confirm_action (not supported via MCP)",
            })
            return

        try:
            result = self.registry.execute(name, args)
            self._send_json(200, {"ok": True, "result": result})
        except Exception as e:
            logger.warning("[MCP] Tool %s failed: %s", name, e)
            self._send_json(500, {"ok": False, "error": str(e)})

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def _make_handler(registry):
    """Factory: bind registry to handler class."""
    cls = _MCPHandler
    cls.registry = registry
    return cls


# ── Server instance management ─────────────────────────────────────

_server_instance: Optional[HTTPServer] = None
_server_thread: Optional[Thread] = None


def start_mcp_server(registry, port: int = MCP_PORT) -> bool:
    """Start MCP HTTP server in a daemon thread.

    Args:
        registry: ToolRegistry instance.
        port: TCP port (default 17328).

    Returns:
        True if started, False if already running.
    """
    global _server_instance, _server_thread

    if _server_instance is not None:
        logger.warning("[MCP] Server already running on port %d", MCP_PORT)
        return False

    handler_cls = _make_handler(registry)

    try:
        _server_instance = HTTPServer(("127.0.0.1", port), handler_cls)
        _server_thread = Thread(
            target=_server_instance.serve_forever,
            name="mcp-server",
            daemon=True,
        )
        _server_thread.start()
        logger.info("[MCP] Server started on http://127.0.0.1:%d", port)
        return True
    except Exception as e:
        logger.warning("[MCP] Failed to start server: %s", e)
        _server_instance = None
        _server_thread = None
        return False


def stop_mcp_server():
    """Stop the MCP HTTP server."""
    global _server_instance, _server_thread

    if _server_instance:
        try:
            _server_instance.shutdown()
        except Exception:
            pass
        _server_instance = None
        _server_thread = None
        logger.info("[MCP] Server stopped")
