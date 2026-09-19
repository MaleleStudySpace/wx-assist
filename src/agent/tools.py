"""Tool executor — wraps project components into callable Agent tools.

Each tool is registered into a ToolRegistry at construction time.
Tools can be added/removed without modifying AgentEngine.
All handlers are synchronous — no async needed.
"""

import json
import logging
import time
from datetime import datetime

from src.assistant.config import (
    load_assistant_config,
    mutate_config,
    AlertGroup,
    DigestChat,
    DigestGroup,
    OAGroup,
    OAMonitorGroup,
    _next_digest_group_id,
    is_regex_keyword,
    validate_alert_keywords,
)
from src.skill.engine import SkillNotFound

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
                 cron_scheduler=None, skill_engine=None,
                 rag=None, content_cache=None, oa_monitor=None,
                 alert_engine=None):
        self._store = store
        self._summarizer = summarizer
        self._status_fn = status_fn
        self._task_center = task_center
        self._scheduler = scheduler
        self._cron_scheduler = cron_scheduler
        self._skill_engine = skill_engine
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

    def set_cron_scheduler(self, cron_scheduler):
        """注入 CronScheduler。注入后自动注册 cron 管理工具。"""
        self._cron_scheduler = cron_scheduler
        self._register_cron_tools()

    def set_skill_engine(self, skill_engine):
        """注入 SkillEngine。注入后自动注册 skill 工具。"""
        self._skill_engine = skill_engine
        self._register_skill_tools()

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

        # ── get_latest_oa_digest ─────────────────────────────────────
        r.register(
            name="get_latest_oa_digest",
            description="【只读查询】获取指定公众号分组最近一次已完成的摘要正文。"
                       "只读取已经生成的摘要，不会重新抓取文章、不调用AI生成、也不发送推送。"
                       "用户要把公众号摘要整理到笔记、查看最近一份公众号摘要时调用。"
                       "默认只查定时任务（scheduler/catchup），精确匹配分组名称。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "公众号摘要分组名称，例如‘开源项目’",
                    },
                    "within_hours": {
                        "type": "integer",
                        "description": "向前查询的时间范围，默认48小时，最大30天",
                        "default": 48,
                    },
                },
                "required": ["group_name"],
            },
            handler=self._handle_get_latest_oa_digest,
        )

        # ── get_latest_group_digest ──────────────────────────────────
        r.register(
            name="get_latest_group_digest",
            description="【只读查询】获取指定群聊摘要分组最近一次已完成的摘要正文。"
                       "只读取已经生成的摘要，不会重新读取消息、不调用AI生成、也不发送推送。"
                       "用户要把群聊摘要整理到笔记、查看最近一份群聊摘要时调用。"
                       "默认只查定时任务（scheduler/catchup），精确匹配分组名称。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "群聊摘要分组名称，例如‘技术交流群’",
                    },
                    "within_hours": {
                        "type": "integer",
                        "description": "向前查询的时间范围，默认48小时，最大30天",
                        "default": 48,
                    },
                },
                "required": ["group_name"],
            },
            handler=self._handle_get_latest_group_digest,
        )

        # ── run_digest (消耗 AI，直接执行) ────────────────────────────
        r.register(
            name="run_digest",
            description="为指定分组或群聊手动生成近期消息摘要。"
                       "优先匹配定时分组名称；若未命中则按单个群聊名称查找。"
                       "用户说'总结一下某某群'、'群里说了什么'时调用。"
                       "直接执行，不需用户二次确认。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "分组名称或群聊名称，例如'聚沙成塔'、'项目群'",
                    },
                    "hours": {
                        "type": "integer",
                        "description": "回看最近多少小时的消息，默认 6（仅在单聊回退时生效，分组走配置里的 lookback_hours）",
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
            description="为指定公众号分组或公众号生成文章摘要。"
                       "优先匹配已配置的公众号分组名称；若未命中则按公众号名称模糊查找。"
                       "用户说'总结某某公众号'、'逛逛GitHub最近的文章'时调用。"
                       "直接执行，不需用户二次确认。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "公众号分组名称或公众号名称，例如'技术前沿'、'逛逛GitHub'",
                    },
                    "hours": {
                        "type": "integer",
                        "description": "回看最近多少小时的文章，默认 24（仅 ad-hoc 模式生效，已配置分组走配置里的 lookback）",
                        "default": 24,
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
                       "系统会生成通知并自动推送到消息推送页中已绑定的平台。"
                       "用户说'帮我盯着某某群的关键词'时调用。"
                       "关键词支持两种写法：1) 普通词——大小写不敏感的子串匹配，"
                       "如 'bug'；2) 正则——首尾用斜杠包裹，如 '/\\d{2,}元/'、"
                       "'/(?i)urgent/'。正则是**大小写敏感**的，需要忽略大小写必须"
                       "在开头写 (?i)，例如 '/(?i)error/'。"
                       "只有用户明确要求模糊/正则/规则匹配时才用正则写法；"
                       "普通词不要加斜杠，否则斜杠本身会被当成正则语法。"
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
                        "description": "要预警的关键词列表，如 ['bug','故障']；"
                                       "正则写法则形如 ['/\\d{2,}元/', '/(?i)urgent/']"
                                       "——首尾斜杠包裹，默认大小写敏感",
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
            description="【分组定时摘要】为指定群聊配置定时消息摘要。"
                       "配置后，每天在设定时间自动生成该分组的聊天摘要并自动推送到消息推送页中已绑定的平台。"
                       "如果该群聊已属于某个定时分组，则更新该分组的配置（影响分组内所有会话）。"
                       "用户说'每天早上9点给我发群摘要'、'帮我总结项目群的消息'时调用。"
                       "这是写操作，会修改系统配置。"
                       "调用前需明确：群聊名称、生成时间（HH:MM）。",
            parameters={
                "type": "object",
                "properties": {
                    "group_name": {
                        "type": "string",
                        "description": "群聊名称，例如'项目群'、'技术交流群'。如果该群已属于某个分组，会更新整个分组",
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
                        "description": "历史兼容字段；业务启用后自动推送到消息推送页中已绑定的平台，留空也会自动推送",
                        "default": "",
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
                       "配置后，每天在设定时间自动总结该分组内所有公众号的最新文章并自动推送到消息推送页中已绑定的平台。"
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
                        "description": "历史兼容字段；业务启用后自动推送到消息推送页中已绑定的平台，留空也会自动推送",
                        "default": "",
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
                       "当该公众号发布新文章时，自动推送通知到消息推送页中已绑定的平台。"
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
                        "description": "历史兼容字段；业务启用后自动推送到消息推送页中已绑定的平台，留空也会自动推送",
                        "default": "",
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

        # ── Skill 工具 ───────────────────────────────────────────────
        if self._skill_engine:
            self._register_skill_tools()

        # ── Cron 定时任务管理工具 ────────────────────────────────────
        # 注：ToolExecutor 创建时 _cron_scheduler 可能还没注入，
        # 注册时判断 None 就跳过，等 set_cron_scheduler() 重新注册。
        # 但 bot.py 目前 init 顺序是 ToolExecutor → MCP → CronScheduler
        # → set_cron_scheduler，所以第一次注册时 cron_scheduler 可能
        # 还没有。不过有 set_cron_scheduler 兜底。
        if self._cron_scheduler:
            self._register_cron_tools()

    # ── 定时任务管理工具的注册方法（可被 set_cron_scheduler 调用）────

    def _register_cron_tools(self) -> None:
        """注册 cron 管理工具。提取为单独方法以便延迟注入后重注册。"""
        r = self.registry
        cs = self._cron_scheduler
        if not cs:
            return

        r.register(
            name="create_cron",
            description="创建定时任务。创建一个按 cron 表达式定时执行 skill 的任务。"
                       "创建前先用 list_skills 查看可用的 skill 名称。"
                       "该操作需要用户确认。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务名称"},
                    "skill": {"type": "string",
                              "description": "skill 名称，先用 list_skills 查看可用 skill"},
                    "cron": {"type": "string",
                             "description": "5字段cron表达式：分 时 日 月 周。"
                             "周日=0。支持 */15(步进)、1-5(范围)、1,3,5(列表)、多行(多个触发时间)。"
                             "例：'0 8 * * *'=每天8点，'0 9 * * 1-5'=工作日9点。"},
                    "args": {"type": "object",
                             "description": "传给 skill 的参数（字典），可选。"
                                            "键名必须匹配 skill 定义中的参数名，"
                                            "先用 list_skills 查看各 skill 的参数定义"},
                    "push_target": {"type": "string", "enum": ["ilink", "qqbot", "feishu", ""],
                                    "description": "历史兼容字段；实际推送到消息推送页中已绑定的平台，留空也会自动推送",
                                    "default": ""},
                    "push_enabled": {"type": "boolean",
                                     "description": "是否发送推送；false 表示静默任务，默认 true",
                                     "default": True},
                },
                "required": ["name", "skill", "cron"],
            },
            handler=self._handle_create_cron,
            requires_confirm=True,
        )
        r.register(
            name="delete_cron",
            description="删除定时任务。需要任务ID。该操作需要用户确认。可用 list_crons 查看任务ID。",
            parameters={"type": "object", "properties": {
                "id": {"type": "string", "description": "任务 ID"},
            }, "required": ["id"]},
            handler=self._handle_delete_cron,
            requires_confirm=True,
        )
        r.register(
            name="list_crons",
            description="查看所有定时任务列表。",
            parameters={"type": "object", "properties": {}},
            handler=self._handle_list_crons,
        )
        r.register(
            name="run_cron",
            description="立即执行一个定时任务（不管 cron 是否到时间）。",
            parameters={"type": "object", "properties": {
                "id": {"type": "string", "description": "任务 ID"},
            }, "required": ["id"]},
            handler=self._handle_run_cron,
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

        lines = [f"共 {len(groups)} 个定时摘要分组："]
        for i, g in enumerate(groups, 1):
            # cron_expr 优先：线上分组基本都是 cron-only，只看 schedule 会显示"未设置"
            sched = g.cron_expr.replace("\n", ", ") if g.cron_expr else (
                ", ".join(g.schedule) if g.schedule else "未设置")
            names = [c.name or c.chat_id for c in g.chats if c.enabled]
            lines.append(
                f"{i}. {g.name}（{len(names)} 个会话）— 时间: {sched}, "
                f"回看: {g.lookback_hours}h"
            )
            if 0 < len(names) <= 3:
                lines.append(f"   会话: {', '.join(names)}")
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
            push_icon = "📮 自动推送到已绑定平台"
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

    # ── digest result lookup ────────────────────────────────────────

    def _get_latest_digest(self, task_type: str, group_name: str,
                           within_hours: int = 48) -> str:
        """Read one completed scheduled digest without triggering generation."""
        if not self._task_center:
            return "摘要查询不可用：任务中心未就绪。"
        normalized_name = str(group_name or "").strip()
        if not normalized_name:
            return "请提供分组名称。"
        task = self._task_center.get_latest_completed_digest(
            task_type, normalized_name, within_hours=within_hours,
            scheduled_only=True,
        )
        if not task:
            kind = "公众号" if task_type == "oa_digest" else "群聊"
            return (f"未找到「{normalized_name}」最近 {within_hours} 小时内"
                    f"已完成的定时{kind}摘要。")

        result = str(task.get("result") or "").strip()
        no_content = (
            not result
            or result in {"无新内容", "无实质内容"}
            or result.startswith("没有新的公众号文章")
            or result.startswith("最近") and "没有新的公众号文章" in result
            or result.startswith("所有文章已摘要过")
        )
        if no_content:
            return json.dumps({
                "ok": True,
                "has_content": False,
                "reason": result or "摘要正文为空",
                "task_id": task.get("id"),
                "task_type": task.get("task_type"),
                "source": task.get("source"),
                "group_id": task.get("group_id"),
                "group_name": task.get("group_name"),
                "created_at": task.get("created_at"),
                "finished_at": task.get("finished_at"),
                "articles_count": task.get("articles_count", 0),
                "msg_count": task.get("msg_count", 0),
            }, ensure_ascii=False)

        return json.dumps({
            "ok": True,
            "has_content": True,
            "task_id": task.get("id"),
            "task_type": task.get("task_type"),
            "source": task.get("source"),
            "group_id": task.get("group_id"),
            "group_name": task.get("group_name"),
            "created_at": task.get("created_at"),
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "articles_count": task.get("articles_count", 0),
            "msg_count": task.get("msg_count", 0),
            "digest": result,
        }, ensure_ascii=False)

    def _handle_get_latest_oa_digest(self, group_name: str,
                                     within_hours: int = 48) -> str:
        return self._get_latest_digest("oa_digest", group_name, within_hours)

    def _handle_get_latest_group_digest(self, group_name: str,
                                        within_hours: int = 48) -> str:
        return self._get_latest_digest("group_digest", group_name, within_hours)

    # ── run_digest (写操作) ────────────────────────────────────────

    def _handle_run_digest(self, group_name: str,
                           hours: int = 6) -> str:
        if not self._store:
            return "无法生成摘要：数据库未就绪"

        # ── 优先匹配定时分组 ──
        dg = self._resolve_digest_group(group_name)
        if dg is not None:
            if not self._scheduler:
                return "摘要调度器未就绪"
            tid = None
            if self._task_center:
                try:
                    tid = self._task_center.create_task(
                        'group_digest', 'agent', dg.id, dg.name,
                    )
                    self._task_center.update_task(tid, status='running', progress='获取消息中')
                except Exception:
                    pass
            try:
                self._scheduler._generate_digest(dg, task_id=tid)
                return f"✅ 已开始为「{dg.name}」生成摘要，完成后将自动推送到已绑定平台。"
            except Exception as e:
                logger.warning("run_digest (group) failed: %s", e)
                if tid:
                    self._task_center.fail_task(tid, error=str(e))
                return f"摘要生成失败: {e}"

        # ── 回退：单聊模式（未配置分组的群聊）──
        chat_id = self._resolve_chat_id(group_name)
        if not chat_id:
            return f"未找到「{group_name}」的分组或消息记录"

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

    def _handle_run_oa_digest(self, group_name: str, hours: int = 24) -> str:
        """为指定公众号分组或公众号生成文章摘要。

        优先匹配已配置的 oa_groups；未命中则按公众号名称模糊查找 oa_accounts，
        构造临时 OAGroup 走 ad-hoc 管线。
        """
        try:
            cfg = load_assistant_config()
        except Exception as e:
            return f"读取配置失败: {e}"

        # ── ① 优先匹配已配置的分组 ──
        oa_group = None
        for g in cfg.oa_groups:
            if g.name.lower() == group_name.lower():
                oa_group = g
                break

        hours_override = None

        if not oa_group:
            # ── ② ad-hoc: 按公众号名称模糊查找 ──
            if not self._content_cache:
                names = '，'.join(g.name for g in cfg.oa_groups) if cfg.oa_groups else '无'
                return (f"未找到公众号分组「{group_name}」\n"
                        f"已配置的分组: {names}\n"
                        f"（内容缓存未就绪，无法按公众号名称查找）")

            try:
                rows = self._content_cache.query(
                    "SELECT gh_id, display_name FROM oa_accounts WHERE display_name LIKE ?",
                    [f"%{group_name}%"],
                )
            except Exception as e:
                return f"查询公众号失败: {e}"

            if not rows:
                names = '，'.join(g.name for g in cfg.oa_groups) if cfg.oa_groups else '无'
                return f"未找到公众号「{group_name}」\n已配置的分组: {names}"

            if len(rows) > 1:
                matches = '\n'.join(f"  - {r['display_name']}" for r in rows[:10])
                return f"找到多个匹配的公众号，请更精确地指定：\n{matches}"

            # 构造临时 OAGroup（不写入配置，仅内存中存在）
            from src.assistant.config import OAGroup
            gh_id = rows[0]['gh_id']
            display_name = rows[0]['display_name']
            oa_group = OAGroup(
                id=f"adhoc_{gh_id}",
                name=display_name,
                accounts=[gh_id],
                lookback_hours=hours,
                lookback_mode="manual",
            )
            hours_override = hours

        tid = None
        if self._task_center:
            try:
                tid = self._task_center.create_task(
                    'oa_digest', 'agent', oa_group.id, oa_group.name,
                )
            except Exception:
                pass

        if not self._scheduler:
            return "摘要调度器未就绪"

        try:
            self._scheduler._generate_oa_digest(
                oa_group, task_id=tid, hours_override=hours_override,
            )
            return f"✅ 已开始为「{oa_group.name}」生成摘要，完成后将自动推送到消息推送页中已绑定的平台。"
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

        # 与配置 API 同一套校验：非法正则不落盘。错误信息里带上写法说明，
        # 因为调用方是 LLM/用户，需要知道「斜杠包裹」这条规则才能修正。
        err = validate_alert_keywords(keywords)
        if err:
            return (
                f"❌ 关键词不合法：{err}\n"
                "写法说明：普通关键词直接写文字（大小写不敏感的子串匹配），"
                "不要加斜杠；需要正则时用斜杠包裹，例如 '/\\d{2,}元/'、"
                "'/(?i)urgent/'（默认大小写敏感，(?i) 需放在开头）。"
            )

        regex_kws = [k for k in keywords if is_regex_keyword(k)]
        outcome: dict = {}

        def _apply(cfg):
            existing = [g for g in cfg.alert_groups if g.group_name == group_name]
            if existing:
                old_count = len(existing[0].keywords)
                existing[0].keywords = list(set(existing[0].keywords + keywords))
                outcome["updated"] = True
                outcome["added"] = len(existing[0].keywords) - old_count
                outcome["count"] = len(existing[0].keywords)
            else:
                cfg.alert_groups.append(AlertGroup(
                    group_name=group_name,
                    keywords=keywords,
                    enabled=True,
                ))
                outcome["updated"] = False

        try:
            cfg = mutate_config(_apply)
        except Exception as e:
            logger.warning("add_alert: mutate config failed: %s", e)
            return f"保存配置失败: {e}"

        if self._alert_engine:
            self._alert_engine.update_config(cfg)

        # 明确回执里有几个是正则条目，让用户/LLM 确认"斜杠被当成了正则"
        # 而不是普通词 —— 否则写错一个斜杠会静默改变匹配语义。
        regex_note = (
            f"\n其中 {len(regex_kws)} 个按正则匹配: {', '.join(regex_kws[:5])}"
            if regex_kws else ""
        )
        if outcome.get("updated"):
            return (
                f"✅ 已更新「{group_name}」的关键词预警\n"
                f"新增 {outcome['added']} 个关键词，当前共 {outcome['count']} 个关键词"
                f"{regex_note}"
            )
        return (
            f"✅ 已为「{group_name}」添加关键词预警\n"
            f"关键词: {', '.join(keywords[:10])}{regex_note}"
        )

    # ── add_digest (写操作) ─────────────────────────────────────────

    def _handle_add_digest(self, group_name: str,
                           schedule: str = "08:00",
                           lookback_hours: int = 6,
                           push_target: str = "") -> str:
        """【群聊定时摘要】配置或更新。"""
        if not group_name:
            return "请提供群聊名称"
        try:
            dt = datetime.strptime(schedule, "%H:%M")
        except ValueError:
            return f"时间格式错误，请使用 HH:MM（如 09:00），收到: {schedule}"

        # 必须先解析出 chat_id：分组模型下会话是显式成员，解析不到就明确报错，
        # 不能像以前那样建一个 chat_id 为空的组（那种组永远跑不出摘要）。
        chat_id = self._resolve_chat_id(group_name)
        if not chat_id:
            return (f"未找到「{group_name}」的会话记录，无法配置定时摘要。"
                    f"请确认群名与微信里显示的完全一致，"
                    f"或到网页端「群聊助手 → 定时群摘要」手动选择会话。")

        # HH:MM → 5 字段 cron。schedule 和 cron_expr 都要写：
        # _should_trigger 优先用 cron_expr，只写 schedule 的组永远不被 cron 触发。
        cron_expr = f"{dt.minute} {dt.hour} * * *"
        outcome: dict = {}

        def _apply(cfg):
            owner = None
            for g in cfg.digest_groups:
                if any(c.chat_id == chat_id for c in g.chats):
                    owner = g
                    break
            if owner is None:
                cfg.digest_groups.append(DigestGroup(
                    id=_next_digest_group_id({g.id for g in cfg.digest_groups}),
                    name=group_name,
                    chats=[DigestChat(chat_id=chat_id, name=group_name)],
                    schedule=[schedule],
                    cron_expr=cron_expr,
                    lookback_hours=lookback_hours,
                    push_target=push_target,
                    enabled=True,
                ))
                outcome.update(created=True, name=group_name, chats=1,
                               chat_names=[group_name])
                return
            old_schedule = ', '.join(owner.schedule) if owner.schedule else '无'
            old_cron = owner.cron_expr or '无'
            chat_names = [c.name or c.chat_id for c in owner.chats]
            owner.schedule = [schedule]
            owner.cron_expr = cron_expr
            owner.lookback_hours = lookback_hours
            owner.push_target = push_target
            owner.enabled = True
            outcome.update(created=False, name=owner.name,
                           chats=len(owner.chats), chat_names=chat_names,
                           old_schedule=old_schedule, old_cron=old_cron)

        try:
            cfg = mutate_config(_apply)
        except Exception as e:
            logger.warning("add_digest: mutate config failed: %s", e)
            return f"保存配置失败: {e}"

        if self._scheduler:
            self._scheduler.update_config(cfg)

        name = outcome["name"]
        if outcome["created"]:
            return (
                f"✅ 已为「{name}」配置分组定时摘要\n"
                f"📅 时间: 每天 {schedule}\n"
                f"⏱ 回看: 最近 {lookback_hours} 小时\n"
                f"📮 推送: 自动推送到已绑定平台"
            )
        lines = [f"✅ 已更新「{name}」的分组定时摘要"]
        old_sched = outcome.get("old_schedule", "")
        if old_sched and old_sched != schedule:
            lines.append(f"📅 时间: {old_sched} → {schedule}")
        else:
            lines.append(f"📅 时间: {schedule}")
        lines.append(f"⏱ 回看: 最近 {lookback_hours} 小时")
        lines.append(f"📮 推送: 自动推送到已绑定平台")
        chat_names = outcome.get("chat_names", [])
        if len(chat_names) > 1:
            lines.append(f"📋 分组包含 {outcome['chats']} 个会话: {', '.join(chat_names)}")
        return '\n'.join(lines)

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

        outcome: dict = {}

        def _apply(cfg):
            existing = [g for g in cfg.oa_groups
                        if g.name.lower() == group_name.lower()]
            if existing:
                g = existing[0]
                g.cron_expr = cron_expr
                g.push_target = push_target
                g.digest_template = template
                g.enabled = True
                outcome["updated"] = True
                return
            cfg.oa_groups.append(OAGroup(
                id=f"grp_{int(time.time())}",
                name=group_name,
                accounts=[],
                cron_expr=cron_expr,
                digest_template=template,
                push_target=push_target,
                lookback_hours=24,
                lookback_mode="auto",
                enabled=True,
            ))
            outcome["updated"] = False

        try:
            cfg = mutate_config(_apply)
        except Exception as e:
            logger.warning("add_oa_scheduled_digest: mutate config failed: %s", e)
            return f"保存配置失败: {e}"

        if self._scheduler:
            self._scheduler.update_config(cfg)

        push_label = "自动推送到已绑定平台"
        if outcome["updated"]:
            return (
                f"✅ 已更新「{group_name}」的公众号定时摘要\n"
                f"📅 时间: 每天 {schedule}\n"
                f"📮 推送: {push_label}\n"
                f"📝 模板: {template}"
            )
        return (
            f"✅ 已为「{group_name}」配置公众号定时摘要\n"
            f"📅 时间: 每天 {schedule}\n"
            f"📮 推送: {push_label}\n"
            f"📝 模板: {template}\n"
            f"💡 如需添加公众号到该分组，请到网页端操作"
        )

    # ── add_oa_monitor (写操作) ────────────────────────────────────

    def _handle_add_oa_monitor(self, account_name: str,
                                push_target: str = "") -> str:
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

        def _apply(cfg):
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
                cfg.oa_monitor_groups.append(OAMonitorGroup(
                    id=f"oam_{int(time.time())}",
                    name=display_name,
                    accounts=[gh_id],
                    enabled=True,
                    push_target=push_target,
                ))

        try:
            cfg = mutate_config(_apply)
        except Exception as e:
            logger.warning("add_oa_monitor: mutate config failed: %s", e)
            return f"保存配置失败: {e}"

        if self._oa_monitor:
            self._oa_monitor.update_config(cfg)

        push_label = "自动推送到已绑定平台"
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

    def _resolve_digest_group(self, group_name: str):
        """Match *group_name* against configured digest_groups.

        Returns the DigestGroup on exact or case-insensitive name match,
        or None if no group matches.
        """
        try:
            cfg = load_assistant_config()
        except Exception as e:
            logger.warning("resolve_digest_group: load config failed: %s", e)
            return None

        target = group_name.strip()
        for dg in cfg.digest_groups:
            if dg.name == target:
                return dg
        lowered = target.lower()
        for dg in cfg.digest_groups:
            if dg.name.lower() == lowered:
                return dg
        return None

    def _resolve_chat_id(self, group_name: str) -> str | None:
        """Resolve a chat display name to its chat_id.

        先查 `data/group_names.json`（chat_id → 显示名 的权威映射，
        `/api/nicknames/groups` 用的就是它），再回落到 messages 表启发式。
        原来只查 `messages.sender_name = 群名`，但 sender_name 是**发言人昵称**
        而不是群名，基本命中不了 —— 这是 agent 群摘要工具一直失效的原因之一。
        """
        if not group_name:
            return None
        target = group_name.strip()

        try:
            import json as _json
            from pathlib import Path as _Path
            p = _Path("data/group_names.json")
            if p.exists():
                mapping = _json.loads(p.read_text(encoding="utf-8"))
                if isinstance(mapping, dict):
                    for cid, name in mapping.items():
                        if isinstance(name, str) and name.strip() == target:
                            return cid
                    lowered = target.lower()
                    for cid, name in mapping.items():
                        if isinstance(name, str) and name.strip().lower() == lowered:
                            return cid
        except Exception as e:
            logger.warning("resolve_chat_id: group_names.json lookup failed for '%s': %s",
                           group_name, e)

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
            return "RAG功能已关闭，请在系统配置中启动；"

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
            return "RAG功能已关闭，请在系统配置中启动；"
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
                f"{i}. [{source} {ts}] {chunk.content}{url_line}"
            )
        return "\n".join(lines)

    # ── search_moments ─────────────────────────────────────────────

    def _handle_search_moments(self, query: str,
                                top_k: int = 5) -> str:
        """语义搜索朋友圈。"""
        if not self._rag:
            return "RAG功能已关闭，请在系统配置中启动；"
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
            return "RAG功能已关闭，请在系统配置中启动；"
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

    # ── Cron + Skill task handlers ──────────────────────────────────

    def _handle_create_cron(self, name: str, skill: str, cron: str,
                            args: dict = None,
                            push_target: str = "",
                            push_enabled: bool = True) -> str:
        """创建定时任务（引用 skill 执行）。"""
        logger.info("[CronTool] create_cron — name=%s skill=%s cron=%s push_enabled=%s args=%s",
                    name, skill, cron, push_enabled, args)
        if not self._cron_scheduler:
            logger.warning("[CronTool] create_cron 失败: cron_scheduler 未就绪")
            return "定时任务系统未就绪"
        if not skill:
            logger.warning("[CronTool] create_cron 失败: skill 为空")
            return "请指定 skill 名称"
        if not cron:
            logger.warning("[CronTool] create_cron 失败: cron 为空")
            return "请指定 cron 表达式"

        # 校验 cron 表达式语法
        from src.utils.cron import validate_cron_syntax
        cron_err = validate_cron_syntax(cron)
        if cron_err:
            logger.warning("[CronTool] create_cron 失败: cron 语法错误 — %s", cron_err)
            return f"❌ cron 表达式格式错误: {cron_err}"

        # 校验 skill 是否存在
        if self._skill_engine:
            try:
                self._skill_engine.get_skill(skill)
            except SkillNotFound:
                logger.warning("[CronTool] create_cron 失败: skill '%s' 不存在", skill)
                return f"❌ skill '{skill}' 不存在，请先用 list_skills 查看可用 skill"

        job = {
            "name": name,
            "skill": skill,
            "cron": cron,
            "push": {"enabled": bool(push_enabled),
                     "target": push_target},
        }
        if args:
            job["args"] = args

        jid = self._cron_scheduler.add_job(job)
        logger.info("[CronTool] create_cron 成功: id=%s name=%s skill=%s cron=%s",
                    jid, name, skill, cron)
        return (f"✅ 已创建定时任务「{name}」\n"
                f"ID: {jid}\n"
                f"Skill: {skill}\n"
                f"Cron: {cron}\n"
                + ("推送: 自动发送到消息推送页中已绑定的平台" if push_enabled
                   else "推送: 静默任务，不发送推送"))

    def _handle_delete_cron(self, id: str) -> str:
        if not self._cron_scheduler:
            logger.warning("[CronTool] delete_cron 失败: cron_scheduler 未就绪")
            return "定时任务系统未就绪"
        ok = self._cron_scheduler.delete_job(id)
        if ok:
            logger.info("[CronTool] delete_cron 成功: id=%s", id)
        else:
            logger.warning("[CronTool] delete_cron 失败: id=%s 不存在", id)
        return f"✅ 已删除任务 {id}" if ok else f"❌ 任务 {id} 不存在"

    def _handle_list_crons(self) -> str:
        if not self._cron_scheduler:
            logger.warning("[CronTool] list_crons 失败: cron_scheduler 未就绪")
            return "定时任务系统未就绪"
        jobs = self._cron_scheduler.list_jobs()
        logger.info("[CronTool] list_crons — 共 %d 个任务", len(jobs))
        if not jobs:
            return "暂无定时任务"
        lines = [f"📋 定时任务 ({len(jobs)} 个):"]
        for j in jobs:
            enabled = "🟢" if j.get("enabled") else "🔴"
            lines.append(
                f"{enabled} [{j.get('id', '?')[:8]}] "
                f"{j.get('name', '?')} (skill:{j.get('skill', '?')}) "
                f"{j.get('cron', '?')}"
            )
        return "\n".join(lines)

    def _handle_run_cron(self, id: str) -> str:
        logger.info("[CronTool] run_cron — id=%s", id)
        if not self._cron_scheduler:
            logger.warning("[CronTool] run_cron 失败: cron_scheduler 未就绪")
            return "定时任务系统未就绪"
        try:
            text = self._cron_scheduler.run_now(id)
            logger.info("[CronTool] run_cron 完成: id=%s result_len=%d", id, len(text or ""))
            return f"✅ 已执行，结果:\n{text[:500]}"
        except Exception as e:
            logger.warning("[CronTool] run_cron 失败: id=%s — %s", id, e)
            return f"❌ 执行失败: {e}"

    # ── Skill 工具 ────────────────────────────────────────────────

    def _register_skill_tools(self) -> None:
        """注册 skill 相关工具（list_skills, execute_skill）。"""
        r = self.registry
        se = self._skill_engine
        if not se:
            return

        r.register(
            name="list_skills",
            description="列出所有可用的 skill 及其描述。",
            parameters={"type": "object", "properties": {}},
            handler=self._handle_list_skills,
        )
        r.register(
            name="execute_skill",
            description="立即执行一个 skill，并返回结果。"
                       "skill 可执行脚本或 AI 任务。"
                       "先用 list_skills 查看可用 skill 和它的参数名和类型。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "skill 名称"},
                    "args": {"type": "object",
                             "description": "参数字典，键名必须匹配 skill 定义中的参数名。"
                                            "先用 list_skills 查看各 skill 的参数定义"},
                },
                "required": ["name"],
            },
            handler=self._handle_execute_skill,
        )

        r.register(
            name="create_skill",
            description="创建 AI 类型的 skill。先用 list_skills 查看是否有 skill-designer，"
                       "如果存在则优先用它辅助设计 skill 的 name、description、prompt，"
                       "设计好后再用此工具创建。如果不存在，直接提供 name(英文名)、"
                       "description(描述)、prompt(完整AI指令) 三个参数调用即可。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "skill 名称，英文，字母数字下划线 2-32 字符",
                    },
                    "description": {
                        "type": "string",
                        "description": "描述这个 skill 做什么",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "AI 执行时的指令，写清楚具体步骤",
                    },
                    "args_schema": {
                        "type": "object",
                        "description": "参数定义（可选），如 {\"city\": {\"type\": \"string\", \"description\": \"城市名\"}}",
                    },
                },
                "required": ["name", "description", "prompt"],
            },
            handler=self._handle_create_skill,
            requires_confirm=True,
        )

    def _handle_list_skills(self) -> str:
        if not self._skill_engine:
            logger.warning("[SkillTool] list_skills 失败: skill_engine 未就绪")
            return "Skill 系统未就绪"
        skills = self._skill_engine.list_skills()
        logger.info("[SkillTool] list_skills — 共 %d 个 skill", len(skills))
        if not skills:
            return "暂无可用 skill"
        lines = [f"📋 Skill ({len(skills)} 个):"]
        for s in skills:
            name = s.get("name", "?")
            desc = s.get("description", "")[:60]
            args = s.get("args", {})
            if args:
                arg_infos = ", ".join(
                    f"{k}({v.get('type', '?')})"
                    for k, v in args.items()
                )
                lines.append(f"  - {name}: {desc}")
                lines.append(f"    参数: {arg_infos}")
            else:
                lines.append(f"  - {name}: {desc}")
        return "\n".join(lines)

    def _handle_execute_skill(self, name: str, args: dict = None) -> str:
        logger.info("[SkillTool] execute_skill — name=%s args=%s", name, args)
        if not self._skill_engine:
            logger.warning("[SkillTool] execute_skill 失败: skill_engine 未就绪")
            return "Skill 系统未就绪"
        try:
            result = self._skill_engine.execute(name, args or {})
            logger.info("[SkillTool] execute_skill 完成: name=%s result_len=%d",
                        name, len(result or ""))
            return result
        except Exception as e:
            logger.warning("[SkillTool] execute_skill 失败: name=%s — %s", name, e)
            return f"❌ Skill 执行失败: {e}"

    def _handle_create_skill(self, name: str, description: str, prompt: str,
                              args_schema: dict = None) -> str:
        """create_skill 工具的处理方法。"""
        logger.info("[SkillTool] create_skill — name=%s description=%s",
                    name, description[:50])
        if not self._skill_engine:
            logger.warning("[SkillTool] create_skill 失败: skill_engine 未就绪")
            return "Skill 系统未就绪"
        try:
            result = self._skill_engine.create_skill(name, description, prompt, args_schema)
            return f"✅ Skill '{name}' 创建成功！路径: {result['path']}"
        except Exception as e:
            logger.warning("[SkillTool] create_skill 失败: name=%s — %s", name, e)
            return f"❌ 创建 Skill 失败: {e}"
