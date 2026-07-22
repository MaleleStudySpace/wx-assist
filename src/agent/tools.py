"""Tool executor — wraps project components into callable Agent tools.

Each tool is registered into a ToolRegistry at construction time.
Tools can be added/removed without modifying AgentEngine.
All handlers are synchronous — no async needed.
"""

import logging
import time
from datetime import datetime

from src.assistant.config import (
    load_assistant_config,
    save_assistant_config,
    AlertGroup,
    DigestGroup,
    OAGroup,
    OAMonitorGroup,
)

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes Agent tools by calling project components directly.

    All tools are registered into ``self.registry`` (a ToolRegistry)
    during ``__init__``.

    Args:
        store: MessageStore instance for DB queries.
        summarizer: AbstractSummarizer instance for AI calls.
        status_fn: Callable returning a status dict snapshot.
        task_center: Optional TaskCenter instance for task tracking.
        scheduler: Optional DigestScheduler instance for OA digest.
    """

    def __init__(self, store, summarizer,
                 status_fn=None, task_center=None, scheduler=None,
                 rag=None, content_cache=None, oa_monitor=None,
                 alert_engine=None):
        self._store = store
        self._summarizer = summarizer
        self._status_fn = status_fn
        self._task_center = task_center
        self._scheduler = scheduler
        self._rag = rag
        self._content_cache = content_cache
        self._oa_monitor = oa_monitor
        self._alert_engine = alert_engine

        from .registry import ToolRegistry
        self.registry = ToolRegistry()
        self._register_all_tools()

    def set_rag(self, rag):
        """Set RAGEngine for search tools. Called after init if RAG available."""
        self._rag = rag

    # ── Registry population ─────────────────────────────────────────

    def _register_all_tools(self) -> None:
        """Register all built-in tools."""
        r = self.registry

        # ── get_status ──────────────────────────────────────────────
        r.register(
            name="get_status",
            description="查看机器人当前运行状态，包括是否在线、数据库健康、"
                       "AI连通性、已处理消息数、群聊数、运行时长。"
                       "用户问'系统正常吗'、'还活着吗'、'什么情况'时调用。",
            parameters={"type": "object", "properties": {}},
            handler=self._handle_get_status,
        )

        # ── list_digests ────────────────────────────────────────────
        r.register(
            name="list_digests",
            description="查看已配置的定时摘要群组列表及其计划。"
                       "用户问'有哪些定时摘要'、'每天早上摘要的群'时调用。",
            parameters={"type": "object", "properties": {}},
            handler=self._handle_list_digests,
        )

        # ── list_alerts ─────────────────────────────────────────────
        r.register(
            name="list_alerts",
            description="查看已配置的关键词预警群组列表。"
                       "用户问'有在盯着哪些群'、'哪些群有预警'时调用。",
            parameters={"type": "object", "properties": {}},
            handler=self._handle_list_alerts,
        )

        # ── list_oa_groups ─────────────────────────────────────────
        r.register(
            name="list_oa_groups",
            description="查看已配置的公众号定时摘要分组列表（非文章实时推送）。"
                       "用户问'有哪些公众号分组'、'配置了哪些公众号摘要'时调用。"
                       "⚠️ 如果要查已开启文章实时推送（新文章即推）的公众号，请调 list_oa_monitors。",
            parameters={"type": "object", "properties": {}},
            handler=self._handle_list_oa_groups,
        )

        # ── list_oa_monitors ────────────────────────────────────────
        r.register(
            name="list_oa_monitors",
            description="查看已开启文章实时推送（新文章发布即推）的公众号列表。"
                       "用户问'我在盯着哪些公众号的动态'、'有哪些实时推送'、"
                       "'哪些公众号开了文章提醒'时调用。"
                       "⚠️ 注意：此工具显示的是文章实时推送配置，不是定时摘要配置。",
            parameters={"type": "object", "properties": {}},
            handler=self._handle_list_oa_monitors,
        )

        # ── list_tasks ──────────────────────────────────────────────
        r.register(
            name="list_tasks",
            description="查看任务中心的执行记录。"
                       "用户问'任务中心有什么'、'正在跑的任务'时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "按状态筛选：running/completed/failed（可选）",
                    },
                    "task_type": {
                        "type": "string",
                        "description": "按类型筛选：group_digest/oa_digest（可选）",
                    },
                },
            },
            handler=self._handle_list_tasks,
        )

        # ── run_digest (消耗 AI，直接执行) ────────────────────────────
        r.register(
            name="run_digest",
            description="为指定群聊手动生成近期消息摘要。"
                       "用户说'总结一下某某群'、'群里说了什么'时调用。"
                       "直接执行，不需用户二次确认。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "群聊名称，例如'项目群'、'技术交流群'",
                    },
                    "hours": {
                        "type": "integer",
                        "description": "回看最近多少小时的消息，默认 6",
                        "default": 6,
                    },
                },
                "required": ["group_name"],
            },
            handler=self._handle_run_digest,
        )

        # ── run_oa_digest (消耗 AI，直接执行) ─────────────────────────
        r.register(
            name="run_oa_digest",
            description="为指定公众号分组生成文章摘要。"
                       "用户说'总结某某公众号'、'公众号有什么新文章'时调用。"
                       "直接执行，不需用户二次确认。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "公众号分组名称",
                    },
                },
                "required": ["group_name"],
            },
            handler=self._handle_run_oa_digest,
        )

        # ── search_oa_accounts (只读查询) ──────────────────────────
        r.register(
            name="search_oa_accounts",
            description="【只读查询】根据公众号显示名称模糊搜索已关注的公众号账号。"
                       "返回匹配的公众号名称和 gh_id。"
                       "在调用 add_oa_monitor 之前，建议先调此工具确认公众号存在。"
                       "用户说'帮我盯着某个公众号'、但不确定名称时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "公众号名称关键词，例如'机器之心'、'AI'",
                    },
                },
                "required": ["query"],
            },
            handler=self._handle_search_oa_accounts,
        )

        # ── add_alert (写操作，需 confirm) ──────────────────────────
        r.register(
            name="add_alert",
            description="为指定群聊添加关键词预警。当群里有人提到这些关键词时，"
                       "系统会生成通知并可选推送到微信。"
                       "用户说'帮我盯着某某群的关键词'时调用。"
                       "这是写操作，会修改系统配置。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "群聊名称",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要预警的关键词列表，如 ['bug','故障']",
                    },
                },
                "required": ["group_name", "keywords"],
            },
            handler=self._handle_add_alert,
            requires_confirm=True,
        )

        # ── add_digest (写操作，需 confirm) ─────────────────────────
        r.register(
            name="add_digest",
            description="【群聊定时摘要】为指定群聊配置定时消息摘要。"
                       "配置后，每天在设定时间自动生成该群的聊天摘要并推送到微信。"
                       "如果该群已存在定时摘要配置，则更新已有配置。"
                       "用户说'每天早上9点给我发群摘要'、'帮我总结项目群的消息'时调用。"
                       "这是写操作，会修改系统配置。"
                       "调用前需明确：群聊名称、生成时间（HH:MM）。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "群聊名称，例如'项目群'、'技术交流群'",
                    },
                    "schedule": {
                        "type": "string",
                        "description": "摘要时间，24小时制 HH:MM 格式，默认 08:00",
                        "default": "08:00",
                    },
                    "lookback_hours": {
                        "type": "integer",
                        "description": "回看最近多少小时的消息，默认 6",
                        "default": 6,
                    },
                    "push_target": {
                        "type": "string",
                        "description": "推送方式：\"ilink\"=推送到微信，\"\"=不推送，默认 \"ilink\"",
                        "default": "ilink",
                    },
                },
                "required": ["group_name"],
            },
            handler=self._handle_add_digest,
            requires_confirm=True,
        )

        # ── add_oa_scheduled_digest (写操作，需 confirm) ──────────────
        r.register(
            name="add_oa_scheduled_digest",
            description="【公众号定时摘要】为指定公众号分组配置定时文章摘要。"
                       "配置后，每天在设定时间自动总结该分组内所有公众号的最新文章并推送到微信。"
                       "如果该分组已存在定时摘要配置，则更新已有配置。"
                       "用户说'每天早上9点总结AI学习的文章'时调用。"
                       "这是写操作，会修改系统配置。"
                       "调用前需先调 list_oa_groups 确认分组存在。"
                       "⚠️ 注意：group_name 是公众号分组名称（如'AI学习'），不是单个公众号名称。"
                       "如果用户说的是单个公众号名（如'机器之心'），请先查它属于哪个分组。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "公众号分组名称，如'AI学习'、'科技快讯'。"
                                       "必须先调 list_oa_groups 确认存在。"
                                       "注意：不是单个公众号名称！",
                    },
                    "schedule": {
                        "type": "string",
                        "description": "生成时间，24小时制 HH:MM 格式，默认 08:00",
                        "default": "08:00",
                    },
                    "push_target": {
                        "type": "string",
                        "description": "推送方式：\"ilink\"=推送到微信，\"\"=不推送，默认 \"ilink\"",
                        "default": "ilink",
                    },
                    "template": {
                        "type": "string",
                        "description": "摘要模板：\"default\"（默认）/\"tech\"/\"entertainment\"",
                        "default": "default",
                    },
                },
                "required": ["group_name"],
            },
            handler=self._handle_add_oa_scheduled_digest,
            requires_confirm=True,
        )

        # ── add_oa_monitor (写操作，需 confirm) ───────────────────────
        r.register(
            name="add_oa_monitor",
            description="【公众号文章更新提醒】为指定公众号开启文章更新推送。"
                       "当该公众号发布新文章时，系统立即推送通知到微信。"
                       "用户说'帮我盯着机器之心的文章更新'、'关注XX公众号的动态'时调用。"
                       "这是写操作，会修改系统配置。"
                       "调用前建议先调 search_oa_accounts 确认公众号名称正确。"
                       "⚠️ 注意：account_name 是公众号显示名称（如'机器之心'），不是分组名。"
                       "如果用户说的名称 search_oa_accounts 搜不到，请让用户确认名称。",
            parameters={
                "type": "object",
                "properties": {
                    "account_name": {
                        "type": "string",
                        "description": "公众号显示名称，如'机器之心'、'量子位'。"
                                       "建议先调 search_oa_accounts 确认名称正确。",
                    },
                    "push_target": {
                        "type": "string",
                        "description": "推送方式：\"ilink\"=推送到微信，\"\"=不推送，默认 \"ilink\"",
                        "default": "ilink",
                    },
                },
                "required": ["account_name"],
            },
            handler=self._handle_add_oa_monitor,
            requires_confirm=True,
        )

        # ── confirm_action（引擎拦截，兜底）────────────────────────
        r.register(
            name="confirm_action",
            description="在执行有副作用的操作（生成摘要、配置预警、配置定时任务等）"
                        "之前，必须调用此工具向用户确认。"
                        "此工具由引擎拦截处理，不需手动执行。",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "要执行的操作名称，如'添加关键词预警'、'生成摘要'",
                    },
                    "details": {
                        "type": "string",
                        "description": "操作详情",
                    },
                },
                "required": ["action", "details"],
            },
            handler=self._handle_confirm_action,
        )

        # ── search_chat_history（语义搜索聊天记录）────────────────
        r.register(
            name="search_chat_history",
            description="语义搜索群聊/私聊历史消息。当用户提到之前讨论过的话题、"
                       "忘记某个结论、或需要查找群聊中说过的话时调用。"
                       "输入自然语言查询，返回相关的聊天片段。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索内容，用自然语言描述你想找的信息，如'上次说的价格是多少'、'关于AI产品经理的讨论'",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量，默认 5",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            handler=self._handle_search_chat_history,
        )

        # ── search_oa_articles（语义搜索公众号文章）─────────────────
        r.register(
            name="search_oa_articles",
            description="语义搜索已关注的公众号文章内容。当用户想回顾某篇文章、"
                       "或查找之前读过的公众号文章时调用。"
                       "输入自然语言查询，返回相关的文章片段。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索内容，用自然语言描述你想找的文章内容",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量，默认 5",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            handler=self._handle_search_oa_articles,
        )

        # ── search_moments（语义搜索朋友圈）─────────────────────────
        r.register(
            name="search_moments",
            description="语义搜索朋友圈历史内容。当用户想找之前看过的朋友圈、"
                       "某个朋友发的动态，或者某个话题的朋友圈时调用。"
                       "输入自然语言查询，返回相关的朋友圈内容。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索内容，用自然语言描述你想找的朋友圈内容",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量，默认 5",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            handler=self._handle_search_moments,
        )

        # ── search_favorites（语义搜索收藏）─────────────────────────
        r.register(
            name="search_favorites",
            description="语义搜索微信收藏中的内容。当用户想找之前收藏的文章、链接、"
                       "聊天记录等时调用。"
                       "输入自然语言查询，返回相关的收藏片段。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索内容，用自然语言描述你想找的收藏内容",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量，默认 5",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            handler=self._handle_search_favorites,
        )

    # ══════════════════════════════════════════════════════════════
    # Tool handlers (all ``def _handle_*(self, ...) -> str``)
    # ══════════════════════════════════════════════════════════════

    # ── get_status ─────────────────────────────────────────────────

    def _handle_get_status(self) -> str:
        if self._status_fn is None:
            return "无法获取状态信息（未注入状态函数）"
        try:
            s = self._status_fn()
        except Exception as e:
            logger.warning("get_status failed: %s", e)
            return f"获取状态失败: {e}"

        running = s.get("running", False)
        return (
            f"运行状态: {'🟢 运行中' if running else '🔴 已停止'}\n"
            f"运行时长: {s.get('uptime_sec', 0) // 60} 分钟\n"
            f"已处理消息: {s.get('messages_processed', 0):,} 条\n"
            f"群聊数: {s.get('group_count', 0)} 个\n"
            f"数据库: {'✅' if s.get('db_ok') else '❌'}\n"
            f"微信在线: {'✅' if s.get('wechat_online') else '❌'}\n"
            f"AI 连通: {'✅' if s.get('ai_ok') else '❌'}\n"
            f"模型: {s.get('model_name', '未配置') or '未配置'}"
        )

    # ── list_digests ───────────────────────────────────────────────

    def _handle_list_digests(self) -> str:
        try:
            cfg = load_assistant_config()
        except Exception as e:
            logger.warning("list_digests failed: %s", e)
            return f"读取配置失败: {e}"

        groups = [g for g in cfg.digest_groups if g.enabled]
        if not groups:
            return "当前没有已配置的定时摘要群组。"

        lines = [f"共 {len(groups)} 个定时摘要群组："]
        for i, g in enumerate(groups, 1):
            sched = ', '.join(g.schedule) if g.schedule else '未设置'
            lines.append(
                f"{i}. {g.group_name} — 时间: {sched}, "
                f"回看: {g.lookback_hours}h"
            )
        return "\n".join(lines)

    # ── list_alerts ────────────────────────────────────────────────

    def _handle_list_alerts(self) -> str:
        try:
            cfg = load_assistant_config()
        except Exception as e:
            logger.warning("list_alerts failed: %s", e)
            return f"读取配置失败: {e}"

        groups = [g for g in cfg.alert_groups if g.enabled]
        if not groups:
            return "当前没有已配置的关键词预警群组。"

        lines = [f"共 {len(groups)} 个预警群组："]
        for i, g in enumerate(groups, 1):
            kws = ", ".join(g.keywords[:5])
            if len(g.keywords) > 5:
                kws += f" 等 {len(g.keywords)} 个关键词"
            lines.append(f"{i}. {g.group_name} — 关键词: {kws}")
        return "\n".join(lines)

    # ── list_oa_groups ───────────────────────────────────────────────

    def _handle_list_oa_groups(self) -> str:
        """查看已配置的公众号定时摘要分组。"""
        try:
            cfg = load_assistant_config()
        except Exception as e:
            logger.warning("list_oa_groups failed: %s", e)
            return f"读取配置失败: {e}"

        groups = [g for g in cfg.oa_groups if g.enabled]
        if not groups:
            return "当前没有已配置的公众号摘要分组。"

        lines = [f"共 {len(groups)} 个公众号摘要分组："]
        for i, g in enumerate(groups, 1):
            accts = ', '.join(g.accounts[:3])
            if len(g.accounts) > 3:
                accts += f" 等 {len(g.accounts)} 个公众号"
            elif not g.accounts:
                accts = "未配置公众号"
            lines.append(f"{i}. {g.name} — {accts}")
        return "\n".join(lines)

    # ── list_oa_monitors ──────────────────────────────────────────────

    def _handle_list_oa_monitors(self) -> str:
        """查看已开启文章实时推送的公众号。"""
        try:
            cfg = load_assistant_config()
        except Exception as e:
            logger.warning("list_oa_monitors failed: %s", e)
            return f"读取配置失败: {e}"

        groups = [g for g in cfg.oa_monitor_groups if g.enabled]
        if not groups:
            return "当前没有已开启文章实时推送的公众号。"

        lines = [f"共 {len(groups)} 个公众号开启了实时推送："]
        for i, g in enumerate(groups, 1):
            accts = ', '.join(g.accounts[:3])
            if len(g.accounts) > 3:
                accts += f" 等 {len(g.accounts)} 个公众号"
            elif not g.accounts:
                accts = "未绑定具体公众号"
            push_icon = "📮 推送到微信" if g.push_target == "ilink" else ""
            lines.append(f"{i}. {g.name} — {accts} {push_icon}".strip())
        return "\n".join(lines)

    # ── list_tasks ─────────────────────────────────────────────────

    def _handle_list_tasks(self, status: str = "",
                           task_type: str = "") -> str:
        if not self._task_center:
            return "任务中心未就绪。"

        try:
            tasks = self._task_center.list_tasks(
                status=status or None,
                task_type=task_type or None,
                limit=20,
            )
        except Exception as e:
            logger.warning("list_tasks failed: %s", e)
            return f"查询任务失败: {e}"

        if not tasks:
            return "当前没有任务记录。"

        STATUS_ICON = {'pending': '⏳', 'running': '🔄',
                       'completed': '✅', 'failed': '❌'}
        TYPE_LABEL = {'group_digest': '群聊摘要', 'oa_digest': '公众号摘要'}

        lines = [f"最近 {len(tasks)} 条任务记录："]
        for t in tasks:
            icon = STATUS_ICON.get(t['status'], '❓')
            label = TYPE_LABEL.get(t['task_type'], t['task_type'])
            lines.append(f"{icon} {label} — {t.get('group_name','')}")
            lines.append(f"   状态: {t['status']} | 进度: {t.get('progress', '')}")
            if t.get('result'):
                lines.append(f"   结果: {t['result'][:50]}")
            if t.get('error'):
                lines.append(f"   错误: {t['error']}")
        return "\n".join(lines)

    # ── run_digest (写操作) ────────────────────────────────────────

    def _handle_run_digest(self, group_name: str,
                           hours: int = 6) -> str:
        if not self._store:
            return "无法生成摘要：数据库未就绪"

        # 查找 chat_id（直接查 messages 表）
        chat_id = self._resolve_chat_id(group_name)
        if not chat_id:
            return f"未找到「{group_name}」的消息记录"

        # 创建 TaskCenter 任务
        tid = None
        if self._task_center:
            try:
                tid = self._task_center.create_task(
                    'group_digest', 'agent', chat_id, group_name,
                )
                self._task_center.update_task(tid, status='running', progress='获取消息中')
            except Exception:
                pass

        try:
            since_ts = int(time.time()) - hours * 3600
            messages = self._store.get_messages_since(chat_id, since_ts, limit=200)
            if not messages:
                if tid:
                    self._task_center.complete_task(tid, result='无新内容')
                return f"「{group_name}」最近 {hours} 小时内没有消息"

            if tid:
                self._task_center.update_task(tid, progress='AI 生成摘要中')

            time_range = (
                f"从 {datetime.fromtimestamp(messages[0]['timestamp']).strftime('%m-%d %H:%M')} "
                f"到 {datetime.fromtimestamp(messages[-1]['timestamp']).strftime('%m-%d %H:%M')}"
            )
            msg_text = "\n".join(
                f"{m.get('sender_name', '?')}: {m.get('content', '')[:200]}"
                for m in messages[-50:]
            )
            prompt = (
                f"以下是群聊「{group_name}」在 {time_range} 的 {len(messages)} 条消息。"
                f"请用简洁的语言概括讨论的核心内容、关键结论和行动项。\n\n{msg_text}"
            )
            summary = self._summarizer.chat(
                message=prompt, requester_name="Agent", group_name=group_name,
            )

            if tid:
                self._task_center.complete_task(
                    tid, result=summary[:200], msg_count=len(messages),
                )

            return (
                f"✅ 「{group_name}」摘要（回看 {hours} 小时, {len(messages)} 条消息）:\n\n"
                f"{summary}"
            )
        except Exception as e:
            logger.warning("run_digest failed: %s", e)
            if tid:
                self._task_center.fail_task(tid, error=str(e))
            return f"摘要生成失败: {e}"

    # ── run_oa_digest (写操作) ─────────────────────────────────────

    def _handle_run_oa_digest(self, group_name: str) -> str:
        """为指定公众号分组生成文章摘要。"""
        try:
            cfg = load_assistant_config()
        except Exception as e:
            return f"读取配置失败: {e}"

        oa_group = None
        for g in cfg.oa_groups:
            if g.name.lower() == group_name.lower():
                oa_group = g
                break
        if not oa_group:
            names = '，'.join(g.name for g in cfg.oa_groups) if cfg.oa_groups else '无配置'
            return f"未找到公众号分组「{group_name}」\n已配置的分组: {names}"

        tid = None
        if self._task_center:
            try:
                tid = self._task_center.create_task(
                    'oa_digest', 'agent', oa_group.id, group_name,
                )
            except Exception:
                pass

        if not self._scheduler:
            return "摘要调度器未就绪"

        try:
            self._scheduler._generate_oa_digest(oa_group, task_id=tid)
            return f"✅ 已开始为「{group_name}」生成摘要，完成后将通过微信通知你。"
        except Exception as e:
            logger.warning("run_oa_digest failed: %s", e)
            if tid:
                self._task_center.fail_task(tid, error=str(e))
            return f"生成失败: {e}"

    # ── add_alert (写操作) ──────────────────────────────────────────

    def _handle_add_alert(self, group_name: str,
                          keywords: list) -> str:
        if not group_name or not keywords:
            return "请提供群聊名称和至少一个关键词"

        try:
            cfg = load_assistant_config()
        except Exception as e:
            logger.warning("add_alert: load config failed: %s", e)
            return f"读取配置失败: {e}"

        existing = [g for g in cfg.alert_groups
                    if g.group_name == group_name]
        if existing:
            old_count = len(existing[0].keywords)
            existing[0].keywords = list(set(existing[0].keywords + keywords))
            new_count = len(existing[0].keywords)
            added = new_count - old_count
            try:
                save_assistant_config(cfg)
            except Exception as e:
                return f"保存配置失败: {e}"
            if self._alert_engine:
                self._alert_engine.update_config(cfg)
            return (
                f"✅ 已更新「{group_name}」的关键词预警\n"
                f"新增 {added} 个关键词，当前共 {new_count} 个关键词"
            )

        cfg.alert_groups.append(AlertGroup(
            group_name=group_name,
            keywords=keywords,
            enabled=True,
        ))
        try:
            save_assistant_config(cfg)
        except Exception as e:
            return f"保存配置失败: {e}"
        if self._alert_engine:
            self._alert_engine.update_config(cfg)

        return (
            f"✅ 已为「{group_name}」添加关键词预警\n"
            f"关键词: {', '.join(keywords[:10])}"
        )

    # ── add_digest (写操作) ─────────────────────────────────────────

    def _handle_add_digest(self, group_name: str,
                           schedule: str = "08:00",
                           lookback_hours: int = 6,
                           push_target: str = "ilink") -> str:
        """【群聊定时摘要】配置或更新。"""
        if not group_name:
            return "请提供群聊名称"
        try:
            datetime.strptime(schedule, "%H:%M")
        except ValueError:
            return f"时间格式错误，请使用 HH:MM（如 09:00），收到: {schedule}"

        try:
            cfg = load_assistant_config()
        except Exception as e:
            logger.warning("add_digest: load config failed: %s", e)
            return f"读取配置失败: {e}"

        # Upsert: match by group_name (case-insensitive)
        existing = [g for g in cfg.digest_groups
                    if g.group_name.lower() == group_name.lower()]
        if existing:
            g = existing[0]
            g.schedule = [schedule]
            g.lookback_hours = lookback_hours
            g.push_target = push_target
            g.enabled = True
            save_assistant_config(cfg)
            if self._scheduler:
                self._scheduler.update_config(cfg)
            push_label = "推送到微信" if push_target == "ilink" else "不推送"
            return (
                f"✅ 已更新「{group_name}」的群聊定时摘要\n"
                f"📅 时间: 每天 {schedule}\n"
                f"⏱ 回看: 最近 {lookback_hours} 小时\n"
                f"📮 推送: {push_label}"
            )

        cfg.digest_groups.append(DigestGroup(
            group_name=group_name,
            schedule=[schedule],
            lookback_hours=lookback_hours,
            push_target=push_target,
            enabled=True,
        ))
        try:
            save_assistant_config(cfg)
        except Exception as e:
            return f"保存配置失败: {e}"

        if self._scheduler:
            self._scheduler.update_config(cfg)
        push_label = "推送到微信" if push_target == "ilink" else "不推送"
        return (
            f"✅ 已为「{group_name}」配置群聊定时摘要\n"
            f"📅 时间: 每天 {schedule}\n"
            f"⏱ 回看: 最近 {lookback_hours} 小时\n"
            f"📮 推送: {push_label}"
        )

    # ── add_oa_scheduled_digest (写操作) ────────────────────────────

    def _handle_add_oa_scheduled_digest(self, group_name: str,
                                         schedule: str = "08:00",
                                         push_target: str = "ilink",
                                         template: str = "default") -> str:
        """【公众号定时摘要】配置或更新。"""
        if not group_name:
            return "请提供公众号分组名称"
        try:
            dt = datetime.strptime(schedule, "%H:%M")
        except ValueError:
            return f"时间格式错误，请使用 HH:MM（如 09:00），收到: {schedule}"

        # HH:MM → 5-field cron: "09:00" → "0 9 * * *"
        cron_expr = f"{dt.minute} {dt.hour} * * *"

        try:
            cfg = load_assistant_config()
        except Exception as e:
            logger.warning("add_oa_scheduled_digest: load config failed: %s", e)
            return f"读取配置失败: {e}"

        # Upsert: match by OAGroup.name (case-insensitive)
        existing = [g for g in cfg.oa_groups
                    if g.name.lower() == group_name.lower()]
        if existing:
            g = existing[0]
            g.cron_expr = cron_expr
            g.push_target = push_target
            g.digest_template = template
            g.enabled = True
            save_assistant_config(cfg)
            if self._scheduler:
                self._scheduler.update_config(cfg)
            push_label = "推送到微信" if push_target == "ilink" else "不推送"
            return (
                f"✅ 已更新「{group_name}」的公众号定时摘要\n"
                f"📅 时间: 每天 {schedule}\n"
                f"📮 推送: {push_label}\n"
                f"📝 模板: {template}"
            )

        # Create new OAGroup
        new_id = f"grp_{int(time.time())}"
        cfg.oa_groups.append(OAGroup(
            id=new_id,
            name=group_name,
            accounts=[],
            cron_expr=cron_expr,
            digest_template=template,
            push_target=push_target,
            lookback_hours=24,
            lookback_mode="auto",
            enabled=True,
        ))
        try:
            save_assistant_config(cfg)
        except Exception as e:
            return f"保存配置失败: {e}"

        if self._scheduler:
            self._scheduler.update_config(cfg)
        push_label = "推送到微信" if push_target == "ilink" else "不推送"
        return (
            f"✅ 已为「{group_name}」配置公众号定时摘要\n"
            f"📅 时间: 每天 {schedule}\n"
            f"📮 推送: {push_label}\n"
            f"📝 模板: {template}\n"
            f"💡 如需添加公众号到该分组，请到网页端操作"
        )

    # ── add_oa_monitor (写操作) ────────────────────────────────────

    def _handle_add_oa_monitor(self, account_name: str,
                                push_target: str = "ilink") -> str:
        """【公众号文章提醒】按公众号名称添加更新提醒。"""
        if not account_name:
            return "请提供公众号名称"

        if not self._content_cache:
            return "内容缓存未就绪，无法查询公众号信息。"

        # Search OA accounts by display_name (fuzzy)
        try:
            rows = self._content_cache.query(
                "SELECT gh_id, display_name FROM oa_accounts WHERE display_name LIKE ?",
                [f"%{account_name}%"],
            )
        except Exception as e:
            logger.warning("add_oa_monitor: query failed: %s", e)
            return f"查询公众号信息失败: {e}"

        if not rows:
            return (
                f"未找到匹配的公众号「{account_name}」。\n"
                f"请先调 search_oa_accounts 搜索确认该公众号是否已缓存，"
                f"或到网页端查看已关注的公众号列表。"
            )

        if len(rows) > 1:
            names = "、".join(f"「{r['display_name']}」" for r in rows[:5])
            extra = f"等 {len(rows)} 个" if len(rows) > 5 else ""
            return (
                f"找到多个匹配的公众号：{names}{extra}。\n"
                f"请指定更精确的名称，或先调 search_oa_accounts 搜索确认。"
            )

        gh_id = rows[0]["gh_id"]
        display_name = rows[0]["display_name"]

        try:
            cfg = load_assistant_config()
        except Exception as e:
            logger.warning("add_oa_monitor: load config failed: %s", e)
            return f"读取配置失败: {e}"

        # Upsert OAMonitorGroup by name
        existing = [g for g in cfg.oa_monitor_groups
                    if g.name.lower() == display_name.lower()]
        if existing:
            g = existing[0]
            g.enabled = True
            g.push_target = push_target
            if gh_id not in g.accounts:
                g.accounts.append(gh_id)
        else:
            new_id = f"oam_{int(time.time())}"
            cfg.oa_monitor_groups.append(OAMonitorGroup(
                id=new_id,
                name=display_name,
                accounts=[gh_id],
                enabled=True,
                push_target=push_target,
            ))

        try:
            save_assistant_config(cfg)
        except Exception as e:
            return f"保存配置失败: {e}"

        if self._oa_monitor:
            self._oa_monitor.update_config(cfg)

        push_label = "推送到微信" if push_target == "ilink" else "不推送"
        return (
            f"✅ 已为「{display_name}」开启文章更新提醒\n"
            f"📮 推送: {push_label}"
        )

    # ── search_oa_accounts (只读查询) ──────────────────────────────

    def _handle_search_oa_accounts(self, query: str) -> str:
        """搜索已缓存的公众号账号。"""
        if not query:
            return "请提供搜索关键词。"
        if not self._content_cache:
            return "内容缓存未就绪。"

        try:
            rows = self._content_cache.query(
                "SELECT gh_id, display_name FROM oa_accounts WHERE display_name LIKE ?",
                [f"%{query}%"],
            )
        except Exception as e:
            logger.warning("search_oa_accounts failed: %s", e)
            return f"搜索失败: {e}"

        if not rows:
            return f"未找到与「{query}」匹配的公众号。"

        lines = [f"🔍 找到 {len(rows)} 个匹配的公众号："]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {r['display_name']} ({r['gh_id']})")
        return "\n".join(lines)

    # ── Internal helpers ──────────────────────────────────────────────

    def _resolve_chat_id(self, group_name: str) -> str | None:
        """Resolve group display name to chat_id via local messages table."""
        try:
            row = self._store.conn.execute(
                "SELECT chat_id FROM messages WHERE sender_name = ? LIMIT 1",
                (group_name,),
            ).fetchone()
            return row["chat_id"] if row else None
        except Exception as e:
            logger.warning("resolve_chat_id failed for '%s': %s", group_name, e)
            return None

    # ── confirm_action（引擎拦截，此 handler 是安全兜底）─────────────

    def _handle_confirm_action(self, action: str = "",
                               details: str = "") -> str:
        """此工具由 AgentEngine 拦截处理，不会真的走到这里。
        如果意外走到了，返回确认提示。"""
        q = f"⚠️ 需要确认：{action}"
        if details:
            q += f"\n{details}"
        q += "\n\n回复「确定」执行，回复「取消」放弃。"
        return q

    # ── search_chat_history ─────────────────────────────────────────

    def _handle_search_chat_history(self, query: str,
                                     top_k: int = 5) -> str:
        """语义搜索聊天记录，返回带群名的结果。"""
        if not self._rag:
            return "搜索服务未就绪（RAG 引擎未初始化）。"

        try:
            results = self._rag.search(query=query, top_k=20, final_k=top_k)
        except Exception as e:
            logger.warning("search_chat_history failed: %s", e)
            return f"搜索失败: {e}"

        if not results:
            return f"没有找到与「{query}」相关的聊天记录。"

        # 解析群名：收集所有 chat_id 后批量查询 display_name
        chat_ids = list(set(
            r.chunk.chat_id for r in results if r.chunk.chat_id
        ))
        group_names: dict[str, str] = {}
        if chat_ids:
            try:
                from src.web.api_handlers import get_wcdb_client
                client = get_wcdb_client()
                if client:
                    names = client.get_display_names(chat_ids)
                    if names:
                        group_names = names
            except Exception as e:
                logger.debug("search_chat_history: get_display_names failed: %s", e)

        lines = [f"找到 {len(results)} 条相关聊天记录："]
        for i, r in enumerate(results, 1):
            chunk = r.chunk
            ts = chunk.created_at
            if len(ts) > 10:
                ts = ts[5:16]  # "MM-DD HH:MM"
            sender = chunk.sender_name or "未知"
            # 群名：优先用 display_name，兜底用 chat_id 后 8 位
            group = group_names.get(chunk.chat_id) or chunk.chat_id[-8:]
            lines.append(
                f"{i}. [{group} {ts} {sender}] {chunk.content[:200]}"
            )

        return "\n".join(lines)

    # ── search_oa_articles ──────────────────────────────────────────

    def _handle_search_oa_articles(self, query: str,
                                    top_k: int = 5) -> str:
        """语义搜索公众号文章。"""
        if not self._rag:
            return "搜索服务未就绪（RAG 引擎未初始化）。"
        try:
            results = self._rag.search(
                query=query, top_k=top_k * 4, final_k=top_k,
                where={"source": "oa"},
            )
        except Exception as e:
            logger.warning("search_oa_articles failed: %s", e)
            return f"搜索失败: {e}"
        if not results:
            return f"没有找到与「{query}」相关的公众号文章。"
        lines = [f"找到 {len(results)} 条相关公众号文章："]
        for i, r in enumerate(results, 1):
            chunk = r.chunk
            ts = chunk.created_at
            if len(ts) > 10:
                ts = ts[5:16]
            source = chunk.chat_id or chunk.sender_name or "公众号"
            url = chunk.source_id or ""
            url_line = f"\n   链接: {url}" if url else ""
            lines.append(
                f"{i}. [{source} {ts}] {chunk.content[:200]}{url_line}"
            )
        return "\n".join(lines)

    # ── search_moments ─────────────────────────────────────────────

    def _handle_search_moments(self, query: str,
                                top_k: int = 5) -> str:
        """语义搜索朋友圈。"""
        if not self._rag:
            return "搜索服务未就绪（RAG 引擎未初始化）。"
        try:
            results = self._rag.search(
                query=query, top_k=top_k * 4, final_k=top_k,
                where={"source": "sns"},
            )
        except Exception as e:
            logger.warning("search_moments failed: %s", e)
            return f"搜索失败: {e}"
        if not results:
            return f"没有找到与「{query}」相关的朋友圈内容。"
        lines = [f"找到 {len(results)} 条相关朋友圈："]
        for i, r in enumerate(results, 1):
            chunk = r.chunk
            ts = chunk.created_at
            if len(ts) > 10:
                ts = ts[5:16]
            sender = chunk.sender_name or "好友"
            lines.append(
                f"{i}. [{sender} {ts}] {chunk.content[:200]}"
            )
        return "\n".join(lines)

    # ── search_favorites ───────────────────────────────────────────

    def _handle_search_favorites(self, query: str,
                                  top_k: int = 5) -> str:
        """语义搜索收藏。"""
        if not self._rag:
            return "搜索服务未就绪（RAG 引擎未初始化）。"
        try:
            results = self._rag.search(
                query=query, top_k=top_k * 4, final_k=top_k,
                where={"source": "fav"},
            )
        except Exception as e:
            logger.warning("search_favorites failed: %s", e)
            return f"搜索失败: {e}"
        if not results:
            return f"没有找到与「{query}」相关的收藏内容。"
        lines = [f"找到 {len(results)} 条相关收藏："]
        for i, r in enumerate(results, 1):
            chunk = r.chunk
            ts = chunk.created_at
            if len(ts) > 10:
                ts = ts[5:16]
            sender = chunk.sender_name or "收藏"
            lines.append(
                f"{i}. [{sender} {ts}] {chunk.content[:200]}"
            )
        return "\n".join(lines)
