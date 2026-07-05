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
                 status_fn=None, task_center=None, scheduler=None):
        self._store = store
        self._summarizer = summarizer
        self._status_fn = status_fn
        self._task_center = task_center
        self._scheduler = scheduler

        from .registry import ToolRegistry
        self.registry = ToolRegistry()
        self._register_all_tools()

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

        # ── search_messages ─────────────────────────────────────────
        r.register(
            name="search_messages",
            description="在聊天记录中搜索消息。用户问'关于xxx说过什么'、"
                       "'找一下xxx的内容'时调用。支持按关键词和发送者搜索。",
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "要搜索的关键词",
                    },
                    "sender": {
                        "type": "string",
                        "description": "发送者名称（可选），只搜索某个人的消息",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回结果数量上限，默认 10",
                        "default": 10,
                    },
                },
                "required": ["keyword"],
            },
            handler=self._handle_search_messages,
        )

        # ── get_messages ────────────────────────────────────────────
        r.register(
            name="get_messages",
            description="获取指定群聊或好友的近期聊天消息。"
                       "用户问'项目群最近说了什么'、'看看群聊记录'时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "群聊或好友名称",
                    },
                    "sender": {
                        "type": "string",
                        "description": "发送者名称（可选），只看某个人的消息",
                    },
                    "hours": {
                        "type": "integer",
                        "description": "回看最近多少小时，默认 24",
                        "default": 24,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回消息数量上限，默认 20",
                        "default": 20,
                    },
                },
                "required": ["group_name"],
            },
            handler=self._handle_get_messages,
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

        # ── run_digest (写操作，需 confirm) ─────────────────────────
        r.register(
            name="run_digest",
            description="为指定群聊手动生成近期消息摘要。"
                       "用户说'总结一下某某群'、'群里说了什么'时调用。"
                       "这是写操作，会消耗 AI 配额。",
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
            requires_confirm=True,
        )

        # ── run_oa_digest (写操作，需 confirm) ──────────────────────
        r.register(
            name="run_oa_digest",
            description="为指定公众号分组生成文章摘要。"
                       "用户说'总结某某公众号'、'公众号有什么新文章'时调用。"
                       "这是写操作，会消耗 AI 配额。",
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
            requires_confirm=True,
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
            description="为指定群聊配置定时消息摘要。到设定时间，系统自动生成"
                       "群聊消息摘要并可选推送到微信。"
                       "用户说'每天早上9点给我发群摘要'时调用。"
                       "这是写操作，会修改系统配置。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "群聊名称",
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
                },
                "required": ["group_name"],
            },
            handler=self._handle_add_digest,
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

    # ── search_messages ────────────────────────────────────────────

    def _handle_search_messages(self, keyword: str,
                                sender: str = "",
                                limit: int = 10) -> str:
        limit = min(limit, 30)  # 硬上限
        if not keyword.strip():
            return "请输入要搜索的关键词"
        if not self._store:
            return "无法搜索：数据库未就绪"

        try:
            results = self._store.search_messages(
                keyword=keyword, sender=sender or None, limit=limit,
            )
        except Exception as e:
            logger.warning("search_messages failed: %s", e)
            return f"搜索失败: {e}"

        if not results:
            suffix = f"（发送者: {sender}）" if sender else ""
            return f"未找到包含「{keyword}」的消息{suffix}"

        lines = [f"找到 {len(results)} 条包含「{keyword}」的消息："]
        for i, m in enumerate(results, 1):
            ts = m.get("timestamp", 0)
            time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "?"
            sname = m.get("sender_name", "?")
            content = m.get("content", "")[:200]
            lines.append(f"{i}. [{time_str}] {sname}: {content}")

        return "\n".join(lines)

    # ── get_messages ───────────────────────────────────────────────

    def _handle_get_messages(self, group_name: str,
                             sender: str = "",
                             hours: int = 24,
                             limit: int = 20) -> str:
        limit = min(limit, 50)  # 硬上限
        if not self._store:
            return "无法获取消息：数据库未就绪"

        try:
            rows = self._store.search_messages(group_name, limit=1)
        except Exception as e:
            logger.warning("get_messages search failed: %s", e)
            return f"搜索群聊失败: {e}"

        if not rows:
            return f"未找到「{group_name}」的消息记录"

        chat_id = rows[0]["chat_id"]
        since_ts = int(time.time()) - hours * 3600
        try:
            messages = self._store.get_messages_since(chat_id, since_ts, limit=limit)
        except Exception as e:
            logger.warning("get_messages failed: %s", e)
            return f"获取消息失败: {e}"

        if not messages:
            return f"「{group_name}」最近 {hours} 小时内没有消息"

        if sender:
            messages = [m for m in messages
                       if m.get("sender_name", "").lower() == sender.lower()]
            if not messages:
                return f"「{group_name}」最近 {hours} 小时内没有 {sender} 的消息"

        lines = [f"「{group_name}」最近 {len(messages)} 条消息："]
        for i, m in enumerate(messages, 1):
            ts = m.get("timestamp", 0)
            time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "?"
            sname = m.get("sender_name", "?")
            content = m.get("content", "")[:200]
            lines.append(f"{i}. [{time_str}] {sname}: {content}")

        return "\n".join(lines)

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

        # 查找 chat_id
        try:
            rows = self._store.search_messages(group_name, limit=1)
        except Exception as e:
            return f"搜索群聊失败: {e}"
        if not rows:
            return f"未找到「{group_name}」的消息记录"
        chat_id = rows[0]["chat_id"]

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

        return (
            f"✅ 已为「{group_name}」添加关键词预警\n"
            f"关键词: {', '.join(keywords[:10])}"
        )

    # ── add_digest (写操作) ─────────────────────────────────────────

    def _handle_add_digest(self, group_name: str,
                           schedule: str = "08:00",
                           lookback_hours: int = 6) -> str:
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

        cfg.digest_groups.append(DigestGroup(
            group_name=group_name,
            schedule=[schedule],
            lookback_hours=lookback_hours,
            enabled=True,
        ))
        try:
            save_assistant_config(cfg)
        except Exception as e:
            return f"保存配置失败: {e}"

        return (
            f"✅ 已为「{group_name}」配置定时摘要\n"
            f"时间: 每天 {schedule}\n"
            f"回看: 最近 {lookback_hours} 小时的消息"
        )

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
