"""Tool Registry — manage tool definitions and handlers at runtime.

Provides ToolDef (one tool's definition) and ToolRegistry (collection
of tools).  Tools can be registered/unregistered dynamically without
modifying the AgentEngine or restarting.

Usage:
    registry = ToolRegistry()

    def my_handler(keyword: str) -> str:
        return f"搜到了: {keyword}"

    registry.register(
        name="my_tool",
        description="搜索某某内容",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "关键词"},
            },
            "required": ["keyword"],
        },
        handler=my_handler,
    )

    schemas = registry.get_all_schemas()
    result = registry.execute("my_tool", {"keyword": "test"})
"""

import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

# Type for a tool handler: synchronous function that takes **kwargs and returns str
Handler = Callable[..., str]


@dataclass
class ToolDef:
    """Immutable tool definition."""

    name: str
    description: str
    parameters: dict  # JSON Schema for the tool's arguments
    handler: Handler  # Synchronous function that takes **args and returns str
    requires_confirm: bool = False  # Write operations need user confirmation

    def to_openai_schema(self) -> dict:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """注册表 — 管理工具定义和执行器。

    Tools can be registered/unregistered at runtime.
    AgentEngine queries the registry for available tool schemas.
    """

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    # ── Registration ──────────────────────────────────────────────

    def register(self, name: str, description: str,
                 parameters: dict, handler: Handler,
                 requires_confirm: bool = False) -> None:
        """Register a tool.

        Args:
            name: Tool name (used by LLM to call it).
            description: What the tool does and when to call it.
            parameters: JSON Schema for arguments.
            handler: Synchronous function that takes **args and returns str.
            requires_confirm: True if this is a write operation.
        """
        if name in self._tools:
            logger.warning("Overwriting existing tool: %s", name)
        self._tools[name] = ToolDef(name, description, parameters, handler, requires_confirm)

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        removed = self._tools.pop(name, None)
        if removed:
            logger.debug("Tool unregistered: %s", name)

    def get(self, name: str) -> ToolDef | None:
        """Get a single tool definition by name."""
        return self._tools.get(name)

    # ── Query ─────────────────────────────────────────────────────

    def get_all_schemas(self) -> list[dict]:
        """Get all tool definitions in OpenAI function calling format.

        This is what gets passed to LLM as the ``tools`` parameter.
        """
        return [t.to_openai_schema() for t in self._tools.values()]

    def get_descriptions(self) -> str:
        """Get human-readable tool descriptions for system prompt.

        Returns a formatted string suitable for injection into
        the Agent's system prompt.
        """
        lines = []
        for t in self._tools.values():
            params_desc = ""
            if t.parameters.get("properties"):
                props = t.parameters["properties"]
                parts = []
                for k, v in props.items():
                    desc = v.get("description", "")
                    req = ""
                    if t.parameters.get("required") and k in t.parameters["required"]:
                        req = " (必填)"
                    parts.append(f"{k}: {desc}{req}")
                if parts:
                    params_desc = " 参数: " + ", ".join(parts)
            lines.append(f"- {t.name}: {t.description}{params_desc}")
        return "\n".join(lines)

    # ── Execution ─────────────────────────────────────────────────

    def execute(self, name: str, args: dict) -> str:
        """Execute a tool by name with given args.

        Args:
            name: Tool name.
            args: Keyword arguments for the handler.

        Returns:
            Formatted result string (the LLM's Observation).
        """
        tool = self._tools.get(name)
        if not tool:
            return f"错误：未知工具「{name}」"

        try:
            return tool.handler(**args)
        except Exception as e:
            logger.error("Tool '%s' raised: %s", name, e)
            return f"工具「{name}」执行出错: {e}"

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())
