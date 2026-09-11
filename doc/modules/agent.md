# Agent 助手

## 1. 功能定位

Agent 是一个可以调用本地工具的对话助手。用户可以通过微信私聊、Web 本地测试或定时 prompt Skill 触发 Agent。Agent 负责理解请求、选择工具、读取结果并生成回复。

```text
用户请求
  → AgentEngine
  → LLM 判断是否调用工具
  → ToolRegistry 执行本地/MCP 工具
  → 结果回填对话
  → LLM 生成最终回复
```

Agent 需要配置可用的 AI Provider；本地工具本身不等于 Agent 在无 AI 时可用。

## 2. 入口

| 入口 | 说明 |
|---|---|
| 微信私聊 | 主要交互入口 |
| `POST /api/agent/test` | 本地测试，不经消息推送 |
| prompt 类型 Skill | 使用 `run_once()`，不保留对话历史 |
| 本地 MCP Server | 向外部客户端提供可读工具 |

## 3. ReAct 循环

默认最多 8 步：

1. LLM 接收 system prompt、历史和工具 schema；
2. 直接回复则结束；
3. 返回工具调用则由 Registry 执行；
4. 工具结果作为 Observation 回填；
5. 继续下一轮，直到完成或达到步数上限。

## 4. 工具类别

- 状态和配置查询；
- 摘要、公众号和任务查询；
- 聊天、公众号、朋友圈、收藏的本地语义搜索；
- 关键词提醒、摘要分组、公众号监控配置；
- Cron 和 Skill 管理；
- MCP 外部工具。

工具列表会随可选的 Cron、Skill 和 MCP 服务注入而变化，当前完整定义以 `src/agent/tools.py` 为准。

## 5. 写操作确认

所有会修改配置、创建任务或删除任务的操作由引擎层确认：

```text
用户提出写请求
  → LLM 调用 confirm_action
  → Agent 返回确认说明
  → 用户回复“确定”才执行
  → 用户回复“取消”则放弃
```

确认不是依赖提示词的软规则，而是 AgentEngine 的状态机约束。通过反向 MCP Server 调用时无法进行交互确认，因此写工具会被拒绝。

## 6. 记忆

- 短期记忆：当前会话最近 10 轮；
- 长期记忆：本地数据库保存的摘要，后续会话最多加载 3 条；
- 达到阈值后由 AI 将短期历史整理为长期记忆；
- `run_once()` 不保留历史，也不触发记忆合并。

## 7. RAG 工具

Agent 可以调用本地语义搜索工具检索：

- 聊天记录；
- 公众号文章；
- 朋友圈内容；
- 收藏内容。

语义索引依赖本地向量模型和索引状态；Agent 最终理解和组织结果仍依赖 AI。

## 8. 代码位置

- AgentEngine：`src/agent/engine.py`
- ToolExecutor：`src/agent/tools.py`
- Registry：`src/agent/registry.py`
- 消息入口：`src/router.py`
- 反向 MCP：`src/agent/mcp_server.py`
