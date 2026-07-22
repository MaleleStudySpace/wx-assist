"""ReAct Loop engine — the core reasoning + acting cycle.

Usage:
    engine = AgentEngine(summarizer, tool_executor)
    reply = engine.run("帮我查下系统状态")
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Memory ──────────────────────────────────────────────────────────
AGENT_MEMORY_DB = Path("data/agent_memory.db")
MEMORY_HISTORY_LIMIT = 10   # 短期记忆达到此阈值后触发总结
MEMORY_LOAD_COUNT = 3       # 每次注入 system prompt 的长期记忆条数

# ── System prompt for the Agent ────────────────────────────────────

AGENT_SYSTEM_PROMPT = """\
你是微信助手 Agent，运行在用户的个人微信机器人上。你不仅能聊天，还能调用工具完成用户的需求。

## 你的身份
- 用户通过微信私聊与你对话
- 你可以调用系统工具获取信息或执行操作

## 可用工具
{tool_descriptions}

## 工作方式
你运行在 ReAct（推理 + 行动）循环中：

1. **Thought** — 分析用户想要什么，需要哪些信息
2. **Action** — 选择最合适的工具来获取信息或执行操作
3. **Observation** — 查看工具返回的结果
4. **Repeat** — 重复直到任务完成，然后回复用户

## 安全规则
- 执行写操作（生成摘要、配置预警、配置定时任务等）之前，必须先调用 confirm_action 获取用户确认
- 用户确认后，才能继续执行写操作工具
- 如果用户取消，回复用户操作已取消
- 查询类操作（查状态、搜索记录、查看配置）不需要确认

## 规则
- 只在确实需要信息时才调用工具，不要过度使用
- 调用工具时必须使用正确的参数
- 如果用户指令不清晰，直接询问澄清
- 不要编造工具返回的结果
- 任务完成后，用自然语言回复用户，不超过 200 字
"""


class AgentEngine:
    """ReAct Loop engine with confirm_action state machine.

    All operations are synchronous.  No async/await needed.
    Runs in iLink receiver's background thread.

    Args:
        summarizer: AbstractSummarizer instance with agent_chat() support.
        tool_executor: ToolExecutor instance.
        max_steps: Maximum ReAct iterations before giving up.
    """

    def __init__(self, summarizer, tool_executor,
                 max_steps: int = 8):
        self._llm = summarizer
        self._tools = tool_executor
        self._max_steps = max_steps
        # Pending confirmation state (confirm_action state machine)
        self._pending_confirm: Optional[dict] = None
        # Bypass flag: allow one step to execute requires_confirm tools
        self._bypass_confirm = False
        # Short-term conversation memory: [(user_msg, agent_reply), ...]
        self._history: list[tuple[str, str]] = []
        self._init_memory_db()

    # ── Public API ─────────────────────────────────────────────────

    def run(self, user_message: str) -> str:
        """Run the ReAct loop and return the final reply.

        If there is a pending confirmation from a previous run(),
        handles the user's response first.

        Args:
            user_message: The user's message content.

        Returns:
            Final reply text to send back to the user.
        """

        # ── Phase 1: Handle pending confirmation ─────────────────────
        if self._pending_confirm is not None:
            logger.info("[Agent] Pending confirm exists, routing to _handle_confirm_response")
            messages = [{"role": "user", "content": user_message}]
            return self._handle_confirm_response(user_message, messages)

        # ── Phase 2: Build messages with memory context ──────────────
        messages: list[dict] = []

        # Inject short-term history (last N exchanges)
        for user_msg, agent_reply in self._history[-MEMORY_HISTORY_LIMIT:]:
            messages.append({"role": "user", "content": user_msg})
            messages.append({"role": "assistant", "content": agent_reply})

        messages.append({"role": "user", "content": user_message})

        # ── Phase 3: ReAct loop ──────────────────────────────────────
        logger.info("[Agent] run() — user_message='%s', history=%d entries",
                    user_message[:80], len(self._history))

        system = self._build_system_prompt()

        logger.info("[Agent] Entering _react_loop — tools=%s",
                    [t["function"]["name"] for t in self._tools.registry.get_all_schemas()])

        reply = self._react_loop(system, messages)

        # ── Save to short-term history ───────────────────────────────
        self._history.append((user_message, reply))
        logger.info("[Agent] History now %d entries", len(self._history))

        # Trigger memory consolidation when threshold reached
        if len(self._history) >= MEMORY_HISTORY_LIMIT:
            self._consolidate_memory()

        return reply

    # ── ReAct loop core ────────────────────────────────────────────

    def _react_loop(self, system: str,
                    messages: list[dict]) -> str:
        """The main ReAct reasoning + acting loop."""
        for step in range(1, self._max_steps + 1):
            logger.info("[Agent] Step %d/%d — messages=%d",
                        step, self._max_steps, len(messages))

            # ── Call LLM ─────────────────────────────────────────
            try:
                content, tool_calls, reasoning = self._llm.agent_chat(
                    system_prompt=system,
                    messages=messages,
                    tools=self._tools.registry.get_all_schemas(),
                )
            except Exception as e:
                logger.exception("[Agent] LLM call failed at step %d", step)
                return f"AI 调用失败：{e}"

            logger.info("[Agent] LLM response — content=%s, tool_calls=%d",
                        content[:60] if content else "(null)",
                        len(tool_calls) if tool_calls else 0)

            # ── LLM chose to reply — task complete ────────────────
            if not tool_calls:
                logger.info("[Agent] Complete after %d steps", step)
                return content or "完成。"

            # ── Process tool calls ────────────────────────────────
            # 1) Check for confirm_action (intercept before execution)
            # 2) Check requires_confirm against bypass flag

            bypass_this_round = self._bypass_confirm
            # Clear bypass so subsequent steps need fresh confirm
            self._bypass_confirm = False

            confirm_tc = None
            action_tcs: list[dict] = []
            for tc in tool_calls:
                tc_name = tc["function"]["name"]
                if tc_name == "confirm_action":
                    confirm_tc = tc
                else:
                    action_tcs.append(tc)

            if confirm_tc is not None:
                # confirm_action is special: don't execute it,
                # instead store pending state and ask user.
                logger.info("[Agent] Intercepting confirm_action — pausing for user confirmation")
                return self._intercept_confirm(system, messages, confirm_tc)

            # ── Execute tools ───────────────────────────────────────
            for tc in action_tcs:
                name = tc["function"]["name"]
                try:
                    args = self._parse_args(tc)
                except json.JSONDecodeError:
                    args = {}

                logger.info("[Agent] Tool call: %s(%s)", name, args)

                # Check requires_confirm
                tool = self._tools.registry.get(name)
                if tool and tool.requires_confirm and not bypass_this_round:
                    logger.info("[Agent] Tool '%s' requires confirm but bypass=False — rejecting", name)
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "",
                        "tool_calls": [tc],
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": f"工具「{name}」是写操作，必须先调用 confirm_action 获取用户确认。",
                    })
                    continue

                # Execute tool
                result = self._tools.registry.execute(name, args)

                logger.info("[Agent] Tool result: %s...",
                            result[:120] if result else "(empty)")

                # Append tool call + result to conversation
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": reasoning,
                    "tool_calls": [tc],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result or "（工具返回为空）",
                })

        # Exceeded max steps
        logger.warning("[Agent] Exceeded max_steps=%d", self._max_steps)
        return "我还在思考中，能再说一遍吗？"

    # ── confirm_action state machine ───────────────────────────────

    CONFIRM_YES_KEYWORDS = (
        "确定", "确认", "好", "是", "嗯", "行",
        "可以", "ok", "yes", "y", "对的", "没错",
        "同意", "要",
    )
    CONFIRM_NO_KEYWORDS = (
        "不", "取消", "算了", "不用", "别",
        "no", "n", "不要",
    )

    def _intercept_confirm(self, system: str,
                           messages: list[dict],
                           confirm_tc: dict) -> str:
        """Intercept a confirm_action call from the LLM.

        Stores the pending confirmation state and returns a question
        to present to the user.  The next call to ``run()`` will
        route through ``_handle_confirm_response()``.

        Returns:
            Confirmation question text for the user.
        """
        try:
            args = json.loads(confirm_tc["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            args = {"action": "执行操作", "details": ""}

        action = args.get("action", "执行操作")
        details = args.get("details", "")

        question = f"⚠️ 需要确认：{action}"
        if details:
            question += f"\n{details}"
        question += "\n\n回复「确定」执行，回复「取消」放弃。"

        # Store the assistant message with the confirm_action call
        messages.append({
            "role": "assistant",
            "content": None,
            "reasoning_content": "",
            "tool_calls": [confirm_tc],
        })

        self._pending_confirm = {
            "question": question,
            "messages": messages,
            "system": system,
            "tool_call_id": confirm_tc["id"],
        }

        logger.info("[Agent] Pending confirm: action=%s details=%s",
                    action, details)
        return question

    def _handle_confirm_response(self,
                                 user_message: str,
                                 fresh_messages: list[dict]) -> str:
        """Handle user's response to a pending confirmation question.

        If the user confirms, injects a tool result and continues
        the ReAct loop.  If cancelled, injects cancellation and
        lets the LLM respond.  If unclear, re-asks the question.

        Args:
            user_message: The user's reply to the confirmation prompt.
            fresh_messages: New messages list for this run.

        Returns:
            Either the continued ReAct loop result, or a re-ask.
        """
        pending = self._pending_confirm
        assert pending is not None, "_handle_confirm_response called with no pending state"

        clean = user_message.strip().lower()
        logger.info("[Agent] _handle_confirm_response — user_response='%s'",
                    clean[:40])

        # ── User confirmed ───────────────────────────────────────
        if self._is_confirm_yes(clean):
            logger.info("[Agent] User CONFIRMED — injecting tool result, continuing ReAct loop")
            pending["messages"].append({
                "role": "tool",
                "tool_call_id": pending["tool_call_id"],
                "content": "用户已确认操作，请继续执行。",
            })
            self._pending_confirm = None
            self._bypass_confirm = True
            return self._react_loop(
                pending["system"], pending["messages"],
            )

        # ── User cancelled ───────────────────────────────────────
        if self._is_confirm_no(clean):
            logger.info("[Agent] User CANCELLED — injecting cancellation")
            pending["messages"].append({
                "role": "tool",
                "tool_call_id": pending["tool_call_id"],
                "content": "用户取消了操作。请告知用户操作已取消。",
            })
            self._pending_confirm = None
            return self._react_loop(
                pending["system"], pending["messages"],
            )

        # ── Unclear — re-ask ─────────────────────────────────────
        logger.info("[Agent] Confirm response unclear, re-asking: '%s'", clean[:60])
        return pending.get("question", "请回复「确定」执行，回复「取消」放弃。")

    @staticmethod
    def _is_confirm_yes(text: str) -> bool:
        """Check if user text means 'yes' to a confirmation."""
        lower = text.lower()
        for kw in AgentEngine.CONFIRM_YES_KEYWORDS:
            if kw in lower:
                return True
        return False

    @staticmethod
    def _is_confirm_no(text: str) -> bool:
        """Check if user text means 'no' to a confirmation."""
        lower = text.lower()
        for kw in AgentEngine.CONFIRM_NO_KEYWORDS:
            if kw in lower:
                return True
        return False

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _parse_args(tc: dict) -> dict:
        """Parse tool call arguments (handles str or dict)."""
        raw = tc["function"].get("arguments")
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def _build_system_prompt(self) -> str:
        """Build the system prompt with tool descriptions and memories."""
        desc = self._tools.registry.get_descriptions()
        prompt = AGENT_SYSTEM_PROMPT.replace("{tool_descriptions}", desc)

        # Append long-term memories
        memories = self._load_memories(MEMORY_LOAD_COUNT)
        if memories:
            memory_lines = "\n".join(f"- {m}" for m in memories)
            prompt += f"\n\n## 对话记忆\n{memory_lines}"

        return prompt

    # ── Public accessors ─────────────────────────────────────────────

    def get_tool_descriptions(self) -> str:
        """Get the current tool descriptions text (for welcome message)."""
        return self._tools.registry.get_descriptions()

    def clear_pending_confirm(self) -> None:
        """Clear any pending confirmation state (on shutdown)."""
        self._pending_confirm = None
        self._bypass_confirm = False

    # ── Memory system ────────────────────────────────────────────────

    def _init_memory_db(self) -> None:
        """Create agent_memory table if not exists."""
        try:
            AGENT_MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(AGENT_MEMORY_DB))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
            conn.commit()
            conn.close()
            logger.info("[Agent] Memory DB initialized")
        except Exception as e:
            logger.warning("[Agent] Failed to init memory DB: %s", e)

    def _load_memories(self, limit: int = 3) -> list[str]:
        """Load recent long-term memories (oldest first)."""
        try:
            conn = sqlite3.connect(str(AGENT_MEMORY_DB))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT summary FROM agent_memory ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            return [r["summary"] for r in reversed(rows)]  # oldest first
        except Exception as e:
            logger.warning("[Agent] Failed to load memories: %s", e)
            return []

    def _save_memory(self, summary: str) -> None:
        """Save a long-term memory entry."""
        try:
            conn = sqlite3.connect(str(AGENT_MEMORY_DB))
            conn.execute("INSERT INTO agent_memory (summary) VALUES (?)", (summary,))
            conn.commit()
            conn.close()
            logger.info("[Agent] Saved memory: %s", summary[:60])
        except Exception as e:
            logger.warning("[Agent] Failed to save memory: %s", e)

    def _consolidate_memory(self) -> None:
        """Summarize short-term history into a long-term memory entry."""
        if not self._history:
            return

        # Build history text for summarization
        history_lines = []
        for user_msg, agent_reply in self._history:
            history_lines.append(f"用户: {user_msg[:100]}")
            history_lines.append(f"助手: {agent_reply[:100]}")
        history_text = "\n".join(history_lines)

        try:
            prompt = (
                "请用一句话总结以下对话中提到的用户需求、偏好或重要信息，"
                "以便后续对话中回顾。如果没有值得记住的内容，回复「无」。\n\n"
                f"{history_text}"
            )
            summary = self._llm.chat(
                message=prompt,
                requester_name="system",
                group_name="记忆总结",
            )
            summary = summary.strip()
            if summary and summary != "无":
                self._save_memory(summary)
            # Clear short-term history regardless
            self._history = []
            logger.info("[Agent] Memory consolidated, history cleared")
        except Exception as e:
            logger.warning("[Agent] Memory consolidation failed: %s", e)
