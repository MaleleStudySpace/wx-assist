"""MCP Server — Standard Model Context Protocol (Streamable HTTP transport).

Exposes the same ToolRegistry as AgentEngine via standard MCP protocol.
Any MCP-compatible client (Claude Desktop, VS Code, etc.) can connect.

Protocol: JSON-RPC 2.0 over HTTP POST
Transport: Streamable HTTP (single POST / endpoint)
Spec: https://spec.modelcontextprotocol.io/

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
MCP_PROTOCOL_VERSION = "2024-11-05"


class _MCPHandler(BaseHTTPRequestHandler):
    """MCP Streamable HTTP handler — single POST / endpoint."""

    # Shared registry reference (set by factory)
    registry = None

    def log_message(self, fmt, *args):
        logger.debug("[MCP] %s", fmt % args)

    def _send_jsonrpc(self, body: dict, status: int = 200):
        """Send a JSON-RPC 2.0 response."""
        resp = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def _jsonrpc_error(self, req_id, code: int, message: str):
        """Send a JSON-RPC 2.0 error response."""
        self._send_jsonrpc({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        })

    def _jsonrpc_result(self, req_id, result: dict):
        """Send a JSON-RPC 2.0 success response."""
        self._send_jsonrpc({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        })

    # ── Handlers ─────────────────────────────────────────────────────

    def _handle_initialize(self, req_id, params: dict):
        """Handle 'initialize' — protocol version negotiation + capability exchange."""
        version = params.get("protocolVersion", MCP_PROTOCOL_VERSION)
        self._jsonrpc_result(req_id, {
            "protocolVersion": version,
            "capabilities": {
                "tools": {},  # We support tools
            },
            "serverInfo": {
                "name": "wx-assist MCP",
                "version": "1.0.0",
            },
        })

    def _handle_tools_list(self, req_id):
        """Handle 'tools/list' — return all registered tools (read-only only)."""
        if not self.registry:
            self._jsonrpc_error(req_id, -32603, "Registry not initialized")
            return

        tools = []
        for name in sorted(self.registry._tools.keys()):
            tool = self.registry.get(name)
            if not tool:
                continue
            # Convert parameters to MCP inputSchema format
            tools.append({
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters,  # Already JSON Schema
            })

        self._jsonrpc_result(req_id, {"tools": tools})

    def _handle_tools_call(self, req_id, params: dict):
        """Handle 'tools/call' — execute a tool.

        Args:
            params: {"name": str, "arguments": dict}
        """
        if not self.registry:
            self._jsonrpc_error(req_id, -32603, "Registry not initialized")
            return

        name = params.get("name", "")
        arguments = params.get("arguments", {})

        if not name:
            self._jsonrpc_error(req_id, -32602, "Missing 'name' in params")
            return

        tool = self.registry.get(name)
        if not tool:
            self._jsonrpc_error(req_id, -32602, f"Unknown tool: {name}")
            return

        # Block tools that require confirm (can't confirm via MCP)
        if tool.requires_confirm:
            self._jsonrpc_result(req_id, {
                "content": [{
                    "type": "text",
                    "text": f"Tool '{name}' requires user confirmation and is not available via MCP.",
                }],
                "isError": True,
            })
            return

        try:
            result_text = self.registry.execute(name, arguments)
            self._jsonrpc_result(req_id, {
                "content": [{
                    "type": "text",
                    "text": result_text,
                }],
            })
        except Exception as e:
            logger.warning("[MCP] Tool %s failed: %s", name, e)
            self._jsonrpc_result(req_id, {
                "content": [{
                    "type": "text",
                    "text": f"Tool execution error: {e}",
                }],
                "isError": True,
            })

    def _handle_ping(self, req_id):
        """Handle 'ping' — health check."""
        self._jsonrpc_result(req_id, {})

    # ── Request dispatch ─────────────────────────────────────────────

    def do_POST(self):
        """Single POST / endpoint for all JSON-RPC 2.0 requests."""
        if self.path != "/":
            self._send_jsonrpc({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32000, "message": "Not found"},
            }, 404)
            return

        # Read and parse JSON-RPC request
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as e:
            self._send_jsonrpc({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }, 400)
            return
        except Exception as e:
            self._send_jsonrpc({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Invalid request: {e}"},
            }, 400)
            return

        # Validate JSON-RPC structure
        req_id = body.get("id")
        method = body.get("method", "")

        if body.get("jsonrpc") != "2.0" or not method:
            self._jsonrpc_error(req_id, -32600, "Invalid Request: missing jsonrpc/method")
            return

        params = body.get("params", {})

        # Dispatch by method
        if method == "initialize":
            self._handle_initialize(req_id, params)
        elif method == "tools/list":
            self._handle_tools_list(req_id)
        elif method == "tools/call":
            self._handle_tools_call(req_id, params)
        elif method == "ping":
            self._handle_ping(req_id)
        else:
            self._jsonrpc_error(req_id, -32601, f"Method not found: {method}")

    def do_OPTIONS(self):
        """CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ── Factory ─────────────────────────────────────────────────────────

def _make_handler(registry):
    """Bind registry to handler class."""
    cls = _MCPHandler
    cls.registry = registry
    return cls


# ── Server instance management ─────────────────────────────────────

_server_instance: Optional[HTTPServer] = None
_server_thread: Optional[Thread] = None


def start_mcp_server(registry, port: int = MCP_PORT) -> bool:
    """Start MCP server in a daemon thread.

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
        logger.info("[MCP] Server started on http://127.0.0.1:%d (Streamable HTTP)", port)
        logger.info("[MCP] Connect with: {\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}")
        return True
    except Exception as e:
        logger.warning("[MCP] Failed to start server: %s", e)
        _server_instance = None
        _server_thread = None
        return False


def stop_mcp_server():
    """Stop the MCP server."""
    global _server_instance, _server_thread

    if _server_instance:
        try:
            _server_instance.shutdown()
        except Exception:
            pass
        _server_instance = None
        _server_thread = None
        logger.info("[MCP] Server stopped")
