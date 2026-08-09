# Agent 系统

## 一句话说明

ReAct 推理循环引擎：用户通过 iLink 私聊发消息，Agent 自主理解意图、调用工具、返回结果。写操作强制用户确认，工具注册一次即可同时被 Agent、外部 MCP 客户端、定时任务共用。

## 架构总览

```
用户微信私聊 / Web 测试 / 定时任务
    │
    ▼
AgentEngine (src/agent/engine.py)
    │  ReAct 循环（最多 8 步）
    │  每步: LLM(agent_chat) → 解析 tool_calls → 执行 → 回填 Observation
    ▼
ToolExecutor (src/agent/tools.py)
    │  持有 ToolRegistry（本地工具）
    │  可选被 ProxyRegistry 包裹（注入 MCP 工具）
    ▼
25 个内置工具 + MCP 注入工具（server__tool）
```

## 调用入口

| 入口 | 路径 | 说明 |
|------|------|------|
| iLink 私聊（主入口） | `router._handle_dm` → `AgentEngine.run()` | 微信发私聊消息给机器人即触发 |
| Web 本地测试 | `POST /api/agent/test` | 不走 iLink，本地验证 Agent 对话/工具调用流程 |
| 定时任务（prompt 型 skill） | `SkillEngine._execute_prompt` → `AgentEngine.run_once()` | cron 触发的 skill 用一次性执行，不保留历史 |
| 外部 MCP 客户端 | `POST http://127.0.0.1:17328` | 通过反向 MCP Server 暴露同一套工具 |

## ReAct 循环（`_react_loop`）

```
循环（最多 max_steps 步，默认 8）:
  1. LLM: agent_chat(system_prompt, messages, tools)
        └─ tools = registry.get_all_schemas()（本地 + MCP 合并）
  2. 无 tool_calls → 返回 content，结束
  3. 有 tool_calls → 逐个处理:
       ├─ confirm_action → 引擎拦截，存 _pending_confirm，返回确认提示
       ├─ 写操作且未确认 → 拒绝执行，注入"必须先调用 confirm_action"
       └─ 正常 → registry.execute(name, args)
            └─ 结果作为 Observation 回填 messages
  4. 回到步骤 1
```

停止条件：
- LLM 直接回复（无工具调用）
- `max_steps` 耗尽 → 返回"我还在思考中"
- system prompt 软约束：连续 3 次调用同一工具无进展必须停止

## 工具系统

### ToolRegistry（`src/agent/registry.py`）

中心注册表，`ToolDef` 包含 `name / description / parameters(JSON Schema) / handler / requires_confirm`。

- `register(name, ...)` — 注册工具，同名覆盖并告警
- `get_all_schemas()` — 输出给 LLM 的 OpenAI function calling 格式
- `get_descriptions()` — 生成 system prompt 可读文本
- `execute(name, args)` — 同步执行 handler；未知工具/异常都返回格式化错误字符串（成为 LLM 的 Observation）

**新增工具只需一行 `r.register(...)`**，Agent、反向 MCP Server、欢迎语自动同步更新。

### 内置工具清单（`src/agent/tools.py`）

基础工具（18 个，构造时注册）：

| 工具名 | 类型 | 需确认 | 用途 |
|--------|:----:|:------:|------|
| `get_status` | 读 | ❌ | 查看机器人运行状态 / 数据库 / AI 连通 |
| `list_digests` | 读 | ❌ | 查看已配置的群定时摘要 |
| `list_alerts` | 读 | ❌ | 查看已配置的关键词预警 |
| `list_oa_groups` | 读 | ❌ | 查看公众号摘要分组 |
| `list_oa_monitors` | 读 | ❌ | 查看公众号实时推送监控 |
| `list_tasks` | 读 | ❌ | 查看任务中心记录 |
| `run_digest` | 写 | ✅ | 手动生成群摘要 |
| `run_oa_digest` | 写 | ✅ | 手动生成公众号摘要 |
| `search_oa_accounts` | 读 | ❌ | 模糊搜索公众号 |
| `add_alert` | 写 | ✅ | 添加关键词预警 |
| `add_digest` | 写 | ✅ | 配置群定时摘要 |
| `add_oa_scheduled_digest` | 写 | ✅ | 配置公众号定时摘要 |
| `add_oa_monitor` | 写 | ✅ | 开启公众号文章推送 |
| `confirm_action` | 特殊 | — | 写操作前向用户确认（引擎拦截） |
| `search_chat_history` | 读 | ❌ | RAG 语义搜索聊天记录 |
| `search_oa_articles` | 读 | ❌ | RAG 搜索公众号文章 |
| `search_moments` | 读 | ❌ | RAG 搜索朋友圈 |
| `search_favorites` | 读 | ❌ | RAG 搜索收藏 |

定时任务工具（4 个，`set_cron_scheduler()` 注入后注册）：

| 工具名 | 类型 | 需确认 | 用途 |
|--------|:----:|:------:|------|
| `create_cron` | 写 | ✅ | 按 cron 表达式创建定时 skill 任务 |
| `delete_cron` | 写 | ✅ | 删除定时任务（按 ID） |
| `list_crons` | 读 | ❌ | 列出所有定时任务 |
| `run_cron` | 写 | ❌ | 立即执行一个定时任务（不管是否到时间） |

Skill 工具（3 个，`set_skill_engine()` 注入后注册）：

| 工具名 | 类型 | 需确认 | 用途 |
|--------|:----:|:------:|------|
| `list_skills` | 读 | ❌ | 列出所有可用 skill 及其描述 |
| `execute_skill` | 写 | ❌ | 立即执行一个 skill 并返回结果 |
| `create_skill` | 写 | ✅ | 创建 AI 类型的 skill |

> `create_cron` / `delete_cron` / `create_skill` 等工具描述中明确提示"该操作需要用户确认"，LLM 会先调用 `confirm_action` 征求用户同意。

### MCP 工具注入

MCP 工具与本地工具共用同一 OpenAI function calling schema 通道，LLM 完全无感知：

```
MCPToolRegistry 包裹本地 registry + MCPServerManager
    │  get_all_schemas() = 本地 schema + MCP schema（{server}__{tool}）
    │  execute(name, args):
    │    名字含 "__" → 拆 server__tool → manager.invoke()
    │    不含 "__"   → 本地执行
    ▼
bot.py 用 ProxyRegistry 替换 tool_executor.registry（接口兼容，无侵入）
```

详见 [mcp-client.md](mcp-client.md)。

## confirm_action 状态机

写操作工具标记 `requires_confirm=True`，引擎层强制拦截（非 LLM 软约束）：

```
用户: "帮我盯着项目群的 bug"
  → LLM 调 confirm_action({action:"添加关键词预警"})
  → 引擎拦截，存 _pending_confirm
  → 返回 "⚠️ 需要确认：添加关键词预警\n\n回复确定执行，回复取消放弃。"
用户: "确定"
  → 关键词匹配（确定/确认/好/是/嗯/行/ok → YES）
  → 直接执行挂起的 action_tcs（不再回 LLM 重新生成）
  → 置 _bypass_confirm 放行后续步骤
用户: "取消" → 注入取消结果
用户: 模糊回复 → 原问题重问
```

## 记忆系统

| 层级 | 存储 | 范围 |
|------|------|------|
| 短期记忆 | 内存 `_history` 列表 | 最近 10 轮对话 |
| 长期记忆 | SQLite `agent_memory` 表（`data/agent_memory.db`） | 跨会话持久 |

短期达到 10 轮后，LLM 自动把历史总结为一条长期记忆存入 DB，下次对话注入 system prompt（最多加载 3 条）。

`run_once()`（定时任务用）不保留历史、不触发记忆合并。

## 与反向 MCP Server 的关系

Agent 使用的同一个 ToolRegistry 可通过标准 MCP 协议暴露给外部客户端（Claude Desktop、VS Code 等）：

- 地址 `http://127.0.0.1:17328`，Streamable HTTP 传输
- 提供 `initialize` / `tools/list` / `tools/call` / `ping`
- `requires_confirm` 的写操作工具被显式拒绝（`isError: true`，"not available via MCP"）——外部客户端无法处理微信确认流程

## 代码位置

| 组件 | 文件 |
|------|------|
| AgentEngine（ReAct + 记忆） | `src/agent/engine.py` |
| ToolExecutor（25 个工具） | `src/agent/tools.py` |
| ToolRegistry | `src/agent/registry.py` |
| 反向 MCP Server | `src/agent/mcp_server.py` |
| iLink DM 路由 | `src/router.py` |
| Agent 进度推送 | `src/bot.py`（`_agent_progress_callback`） |
