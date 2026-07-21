"""MCP server 生命周期管理。

职责：
- 根据配置启动/停止 MCP client
- 心跳检测 (ping 每 5s)
- 超时降级（连续 3 次失败摘除工具）
- 自动恢复（降级后每 5 分钟重试）
- 状态变更通知（给 server.py 的 status_updater 回调）
"""

import json
import logging
import os
import threading
import time

from src.mcp.client import create_client, MCPClient
from src.mcp.config_schema import validate_config

logger = logging.getLogger(__name__)


class MCPServerManager:
    """MCP server 管理器。"""

    def __init__(self):
        self._clients = {}      # name → MCPClient
        self._name_map = {}     # name → config (仅活跃的)
        self._disabled_configs = {}  # name → config (禁用的)
        self._tool_table = []   # 平铺工具表: [{server, name, schema}, ...] (已过滤禁用)
        self._all_tool_table = []  # 完整工具表（含禁用，用于 UI 展示）
        self._consecutive_errors = {}  # name → int
        self._degraded = set()  # set of degraded server names
        self._heartbeat_thread = None
        self._stop_heartbeat = threading.Event()
        self._status_updater = None  # callback: status_dict → None
        self._lock = threading.Lock()

    # ── 初始化 ──────────────────────────────────────────────────────────

    def init_from_config(self, config_path: str = None, configs: list = None):
        """从配置文件路径或配置列表初始化全部 MCP server。

        支持两种调用方式：
        - init_from_config(configs=items): 直接传列表
        - init_from_config(config_path="data/user_mcp.json"): 从文件读
        """
        items = configs
        if config_path:
            items = self._load_config(config_path)

        if not items:
            logger.info("[MCP] 无 MCP server 配置，跳过初始化")
            return {"ok": True, "count": 0, "errors": {}}

        # 校验
        result = validate_config(items)
        if not result["ok"]:
            logger.warning("[MCP] 配置校验有误: %s", result["errors"])

        started = 0
        errors = {}
        for item in result["valid_items"]:
            if not item.get("enabled", True):
                logger.info("[MCP] %s: 已禁用，跳过", item["name"])
                errors[item["name"]] = "disabled"
                with self._lock:
                    self._disabled_configs[item["name"]] = item
                continue
            try:
                self._start_one(item)
                started += 1
            except Exception as e:
                logger.warning("[MCP] %s: 启动失败: %s", item["name"], e)
                errors[item["name"]] = str(e)

        if started > 0:
            self._start_heartbeat()

        logger.info("[MCP] 初始化完成: %d/%d 启动成功", started, len(result["valid_items"]))
        return {"ok": True, "count": started, "errors": errors}

    def _load_config(self, path: str) -> list:
        """从 JSON 文件读取 MCP 配置列表。"""
        if not os.path.exists(path):
            logger.info("[MCP] 配置文件不存在: %s", path)
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("servers", data if isinstance(data, list) else [])
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[MCP] 读配置失败 %s: %s", path, e)
            return []

    def _start_one(self, config: dict):
        """启动单个 MCP server。"""
        name = config["name"]
        client = create_client(config)

        # initialize (握手机超时 10s)
        client.initialize()

        # tools/list
        all_tools = client.list_tools()
        logger.info("[MCP] %s: %d tools", name, len(all_tools))

        # 过滤禁用的工具
        disabled = set(config.get("disabled_tools", []))
        tools = [t for t in all_tools if t["name"] not in disabled]
        if len(disabled) > 0:
            skipped = len(all_tools) - len(tools)
            if skipped:
                logger.info("[MCP] %s: 过滤 %d 个禁用工具", name, skipped)

        # 注册到 _clients + _tool_table
        with self._lock:
            self._clients[name] = client
            self._name_map[name] = config
            self._consecutive_errors[name] = 0
            self._degraded.discard(name)
            # 清除旧工具条目防重复，再追加新的
            self._tool_table = [t for t in self._tool_table if t["server"] != name]
            self._all_tool_table = [t for t in self._all_tool_table if t["server"] != name]
            for t in all_tools:
                entry = {
                    "server": name,
                    "name": t["name"],
                    "schema": {
                        "type": "function",
                        "function": {
                            "name": "{}{}{}".format(name, "__", t["name"]),
                            "description": t.get("description", ""),
                            "parameters": t.get("inputSchema", {}),
                        },
                    },
                    "disabled": t["name"] in disabled,
                }
                # 完整工具表（含禁用，用于 UI）
                self._all_tool_table.append(entry)
                if not entry["disabled"]:
                    self._tool_table.append(entry)
                    logger.debug("[MCP] 注册工具: %s__%s", name, t["name"])

        self._notify_status()

    # ── 心跳 + 降级 ─────────────────────────────────────────────────────

    def _start_heartbeat(self):
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True,
            name="mcp-heartbeat"
        )
        self._heartbeat_thread.start()
        logger.info("[MCP] 心跳线程已启动")

    def _stop_heartbeat_loop(self):
        self._stop_heartbeat.set()

    def _heartbeat_loop(self):
        while not self._stop_heartbeat.is_set():
            self._heartbeat_tick()
            self._stop_heartbeat.wait(5)

    def _heartbeat_tick(self):
        with self._lock:
            names = list(self._clients.keys())

        for name in names:
            client = self._clients.get(name)
            if client is None:
                continue

            ok = client.ping()

            with self._lock:
                if ok:
                    self._consecutive_errors[name] = 0
                    if name in self._degraded:
                        # 已恢复 → 重新 list_tools
                        try:
                            tools = client.list_tools()
                            self._tool_table = [
                                t for t in self._tool_table if t["server"] != name
                            ]
                            for t in tools:
                                self._tool_table.append({
                                    "server": name,
                                    "name": t["name"],
                                    "schema": {
                                        "type": "function",
                                        "function": {
                                            "name": "{}{}{}".format(name, "__", t["name"]),
                                            "description": t.get("description", ""),
                                            "parameters": t.get("inputSchema", {}),
                                        },
                                    },
                                })
                            self._degraded.discard(name)
                            logger.info("[MCP] %s: 已恢复，工具表重新注入", name)
                        except Exception as e:
                            logger.warning("[MCP] %s: 恢复后 list_tools 失败: %s", name, e)
                else:
                    self._consecutive_errors[name] = self._consecutive_errors.get(name, 0) + 1
                    n_err = self._consecutive_errors[name]
                    if n_err >= 3 and name not in self._degraded:
                        # 降级：摘除工具
                        self._tool_table = [t for t in self._tool_table if t["server"] != name]
                        self._degraded.add(name)
                        logger.warning("[MCP] %s: ping %d 次失败，已降级摘除",
                                       name, n_err)

        self._notify_status()

    # ── 外部 API ────────────────────────────────────────────────────────

    def add(self, config: dict):
        """热加一个 MCP server (运行时)。"""
        # 先校验
        from src.mcp.config_schema import validate_config
        result = validate_config([config])
        if not result["ok"] or not result["valid_items"]:
            raise ValueError("配置无效: {}".format(result["errors"]))

        item = result["valid_items"][0]
        if item["name"] in self._clients:
            raise ValueError("名称已存在: {}".format(item["name"]))

        # 如果之前是禁用的，从禁用列表移除
        with self._lock:
            self._disabled_configs.pop(item["name"], None)

        try:
            self._start_one(item)
        except Exception:
            raise
        finally:
            self._start_heartbeat()

        # 持久化
        self._persist_config()

    def remove(self, name: str):
        """热删一个 MCP server。"""
        with self._lock:
            client = self._clients.pop(name, None)
            self._name_map.pop(name, None)
            self._tool_table = [t for t in self._tool_table if t["server"] != name]
            self._degraded.discard(name)
            self._consecutive_errors.pop(name, None)
            self._disabled_configs.pop(name, None)

        if client:
            client.close()

        self._persist_config()
        self._notify_status()

    def restart(self, name: str):
        """重启单个 MCP server。"""
        with self._lock:
            old_client = self._clients.pop(name, None)
            config = self._name_map.get(name)

        if old_client:
            old_client.close()

        if not config:
            raise ValueError("不存在的 server: {}".format(name))

        try:
            self._start_one(config)
        except Exception:
            raise

        self._notify_status()

    def disable(self, name: str):
        """禁用 MCP server：停止客户端，保留配置。"""
        with self._lock:
            client = self._clients.pop(name, None)
            config = self._name_map.pop(name, None)
            if config is None:
                config = self._disabled_configs.get(name)
            if config is None:
                raise ValueError("不存在的 server: {}".format(name))
            # 标记禁用
            config["enabled"] = False
            self._disabled_configs[name] = config
            # 摘除工具
            self._tool_table = [t for t in self._tool_table if t["server"] != name]
            self._degraded.discard(name)
            self._consecutive_errors.pop(name, None)

        if client:
            client.close()

        self._persist_config()
        self._notify_status()

    def enable(self, name: str):
        """启用 MCP server：从禁用列表取出，重新启动。"""
        with self._lock:
            config = self._disabled_configs.pop(name, None)
        if config is None:
            raise ValueError("不存在的或未被禁用的 server: {}".format(name))

        config["enabled"] = True
        try:
            self._start_one(config)
        except Exception:
            # 启动失败 → 放回禁用列表
            config["enabled"] = False
            with self._lock:
                self._disabled_configs[name] = config
            raise

        self._persist_config()

    def toggle_tool(self, name: str, tool_name: str, disabled: bool = None):
        """启用/禁用 MCP server 的某个工具。

        Args:
            name: MCP server 名称
            tool_name: 工具名
            disabled: True=禁用, False=启用, None=自动切换

        更新 config 的 disabled_tools 列表，刷新工具表，持久化。
        不重新 list_tools — 避免持锁时调 MCP 子进程导致死锁。
        """
        with self._lock:
            config = self._name_map.get(name)
            if config is None:
                config = self._disabled_configs.get(name)
            if config is None:
                raise ValueError("不存在的 server: {}".format(name))

            disabled_tools = set(config.get("disabled_tools", []))
            if disabled is None:
                disabled = tool_name not in disabled_tools

            if disabled:
                disabled_tools.add(tool_name)
            else:
                disabled_tools.discard(tool_name)

            config["disabled_tools"] = sorted(disabled_tools)

            # 只更新 _all_tool_table 的 disabled 标志 + 同步 _tool_table
            for entry in self._all_tool_table:
                if entry["server"] == name and entry["name"] == tool_name:
                    entry["disabled"] = disabled
                    break

            # 重建 _tool_table（全部从 _all_tool_table 取，去除非禁用）
            self._tool_table = [t for t in self._all_tool_table
                                if t["server"] != name or not t["disabled"]]

        self._persist_config()
        self._notify_status()

    def shutdown_all(self):
        """关闭全部 MCP server (bot 清理时调用)。"""
        self._stop_heartbeat_loop()
        with self._lock:
            names = list(self._clients.keys())
            for name in names:
                client = self._clients.pop(name, None)
                if client:
                    client.close()
            self._tool_table.clear()
            self._degraded.clear()
            self._consecutive_errors.clear()
            self._name_map.clear()
        logger.info("[MCP] 全部 server 已关闭")

    # ── 状态查询 ────────────────────────────────────────────────────────

    def get_tool_table(self):
        with self._lock:
            return list(self._tool_table)

    def get_status(self) -> dict:
        """返回所有 MCP server 状态 dict (用于 WebSocket 广播)。"""
        status = {}
        with self._lock:
            all_names = set(list(self._clients.keys()) + list(self._name_map.keys()) + list(self._degraded) + list(self._disabled_configs.keys()))
            for name in all_names:
                client = self._clients.get(name)
                config = self._name_map.get(name) or self._disabled_configs.get(name)
                tools_count = sum(
                    1 for t in self._tool_table if t["server"] == name
                )
                if name in self._degraded:
                    st = "degraded"
                    err = "ping 3 次失败"
                elif name in self._disabled_configs and name not in self._clients:
                    st = "stopped"
                    err = "已禁用"
                elif client and client.connected:
                    st = "running"
                    err = ""
                elif client and not client.connected:
                    st = "stopped"
                    err = "手动停止"
                else:
                    st = "error"
                    err = "初始化失败"
                status[name] = {
                    "status": st,
                    "transport": config.get("transport", "stdio") if config else "unknown",
                    "tools_count": tools_count,
                    "error": err,
                }
        return status

    def register_status_updater(self, callback):
        """注册状态变更回调 (由 server.py 调用)。"""
        self._status_updater = callback

    def invoke(self, server_name: str, tool_name: str, args: dict):
        """调用 MCP server 的某个工具。

        Args:
            server_name: MCP server 名称
            tool_name: 工具名 (不含 server__ 前缀)
            args: 参数字典

        Returns:
            result dict (含 content 列表)

        Raises:
            ValueError: server 不存在或被降级
            TimeoutError / ConnectionError: 调用失败
        """
        with self._lock:
            if server_name in self._degraded:
                raise RuntimeError("{}: server 已被降级".format(server_name))
            client = self._clients.get(server_name)
            config = self._name_map.get(server_name)

        if client is None:
            raise ValueError("不存在的 server: {}".format(server_name))

        timeout = config.get("timeout", 30) if config else 30
        try:
            result = client.call_tool(tool_name, args, timeout=timeout)

            # 记录成功，重置错误计数
            with self._lock:
                if server_name in self._consecutive_errors:
                    self._consecutive_errors[server_name] = 0

            return result
        except (TimeoutError, ConnectionError, RuntimeError) as e:
            # 记录错误
            with self._lock:
                self._consecutive_errors[server_name] = self._consecutive_errors.get(server_name, 0) + 1
                n_err = self._consecutive_errors[server_name]
                if n_err >= 3 and server_name not in self._degraded:
                    self._tool_table = [t for t in self._tool_table if t["server"] != server_name]
                    self._degraded.add(server_name)
                    logger.warning("[MCP] %s: 调用失败 %d 次，已降级摘除", server_name, n_err)

            self._notify_status()
            raise

    def _notify_status(self):
        """通知外部 (server.py) 状态变更。"""
        if self._status_updater:
            try:
                self._status_updater(self.get_status())
            except Exception as e:
                logger.warning("[MCP] 状态通知回调异常: %s", e)

    # ── 持久化 ──────────────────────────────────────────────────────────

    def _persist_config(self):
        """将当前配置写回 data/user_mcp.json（包括禁用的 server）。"""
        path = "data/user_mcp.json"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            items = []
            with self._lock:
                for name, config in self._name_map.items():
                    items.append(config)
                for name, config in self._disabled_configs.items():
                    items.append(config)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"servers": items}, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("[MCP] 持久化配置失败: %s", e)

    def get_configs(self) -> list:
        """返回当前所有配置 (活跃 + 禁用，用于 API 查询)。"""
        with self._lock:
            active = list(self._name_map.values())
            disabled = list(self._disabled_configs.values())
            # 为每个 config 注入 tools 列表（含禁用工具，前端自己判断显示）
            result = []
            for c in active:
                c["tools"] = [t for t in self._all_tool_table if t["server"] == c["name"]]
                result.append(c)
            for c in disabled:
                c["tools"] = []
                result.append(c)
            return result
