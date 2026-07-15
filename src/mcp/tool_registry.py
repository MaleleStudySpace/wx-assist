"""MCP tools ↔ LLM function calling schema 转换 + dispatch。

MCPToolRegistry 包裹 wx-assist 原有的 ToolRegistry (src/agent/registry.py)，
将 MCP 工具以相同 schema 格式注入，使 LLM 无感知差异。

在 bot.py 的启动序列中，MCPToolRegistry 取代原 ToolExecutor.registry 与 AgentEngine 对接。
"""

import logging
import re

logger = logging.getLogger(__name__)

# MCP 工具名前缀分隔符
SEP = "__"

# 工具名合法性正则 (server__tool，只含字母数字下划线)
_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


class MCPToolRegistry:
    """MCP 工具注册表：包裹本地 ToolRegistry，注入 MCP 工具。

    提供与 ToolRegistry 兼容的 get_all_schemas() 和 execute() 接口，
    使 AgentEngine 无感。
    """

    def __init__(self, local_registry, mcp_manager=None):
        """
        Args:
            local_registry: src/agent/registry.ToolRegistry 实例
            mcp_manager: src.mcp.manager.MCPServerManager 实例 (可为 None，表示无 MCP)
        """
        self._local = local_registry
        self._mcp_manager = mcp_manager
        self._mcp_schemas = []  # list[dict] — LLM function calling schema 格式

    def refresh(self):
        """从 manager 拉最新工具表，构建 MCP schema。"""
        if not self._mcp_manager:
            self._mcp_schemas = []
            return

        tool_table = self._mcp_manager.get_tool_table()

        # 检测冲突：检查是否有重名的 qual_name
        seen = {}
        for entry in tool_table:
            qn = entry["schema"]["function"]["name"]
            if qn in seen:
                logger.warning("[MCP] 工具名冲突 '%s' (来自 %s 和 %s)，跳过后续注册",
                               qn, seen[qn], entry["server"])
                continue
            seen[qn] = entry["server"]

        self._mcp_schemas = [entry["schema"] for entry in tool_table if entry["schema"]["function"]["name"] in seen]
        logger.info("[MCP] 已注入 %d 个 MCP 工具", len(self._mcp_schemas))

    def get_all_schemas(self) -> list:
        """返回 LLM 的 tools 参数完整列表 (本地 + MCP)。"""
        local_schemas = self._local.get_all_schemas()

        # 如果 MCP 已注入但不在 tool_table 中，refresh
        if self._mcp_manager and not self._mcp_schemas:
            self.refresh()

        return local_schemas + self._mcp_schemas

    def get_descriptions(self) -> str:
        """返回人类可读的工具列表 (用于 LLM system prompt)。"""
        local_desc = self._local.get_descriptions()
        if not self._mcp_schemas:
            return local_desc

        mcp_lines = []
        for s in self._mcp_schemas:
            fn = s["function"]
            desc = fn.get("description", "").replace("\n", " ")
            param_hint = self._param_summary(fn.get("parameters", {}))
            mcp_lines.append("  - {} {} {}".format(fn["name"], param_hint, desc))

        return local_desc + "\n\nMCP 工具:\n" + "\n".join(mcp_lines)

    def execute(self, name: str, args: dict):
        """路由分发：MCP 工具走 manager，本地工具走 local registry。"""
        if SEP in name:
            return self._dispatch_mcp(name, args)
        else:
            return self._local.execute(name, args)

    def _dispatch_mcp(self, name: str, args: dict):
        """拆 server__tool，调用 manager.invoke。"""
        if not self._mcp_manager:
            raise RuntimeError("MCP 管理器未初始化，无法调 MCP 工具: {}".format(name))

        server, tool = name.split(SEP, 1)
        result = self._mcp_manager.invoke(server, tool, args)

        # manager invoke 返回 result dict → LLM 要 text
        content = result.get("content", [])
        texts = [c["text"] for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else str(result)

    @staticmethod
    def _param_summary(params: dict) -> str:
        """从 JSON Schema 提取参数概要。"""
        props = params.get("properties", {})
        required = params.get("required", [])
        parts = []
        for name, info in props.items():
            r = "(必填)" if name in required else "(可选)"
            t = info.get("type", "str")
            parts.append("{init}{name} {t} {r}".format(
                init=":", name=name, t=t, r=r))
        return " ".join(parts)


class ProxyRegistry:
    """透明代理：将本地 ToolRegistry 的 get_all_schemas()/execute() 替换为 MCP 增强版。

    对于 register()/unregister()/get() 等非关键方法，透传到原始本地 registry。
    用于 bot.py 中替换 tool_executor.registry，使 AgentEngine 无感知。
    """

    def __init__(self, local_registry, mcp_wrapper):
        """
        Args:
            local_registry: src/agent/registry.ToolRegistry 实例
            mcp_wrapper: MCPToolRegistry 实例
        """
        self._local = local_registry
        self._mcp = mcp_wrapper

    def get_all_schemas(self):
        return self._mcp.get_all_schemas()

    def get_descriptions(self):
        return self._mcp.get_descriptions()

    def execute(self, name, args):
        return self._mcp.execute(name, args)

    def __getattr__(self, name):
        """未覆盖的方法透传到原始 registry（register, unregister, get 等）。"""
        return getattr(self._local, name)
