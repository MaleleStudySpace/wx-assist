# AI 后端（Summarizer / Provider Detection）

## 一句话说明

统一的 AI 调用层，支持 DeepSeek / Claude / 任意 OpenAI 兼容 API，封装摘要（map-reduce）、对话（SSE 流式）、记忆压缩等能力。

## Provider 选择

### 优先级 1：统一 AI Provider（推荐）

```
AI_PROVIDER_BASE_URL + AI_PROVIDER_API_KEY 都设置
    │
    ├─ type == "auto" → 自动检测
    │   Step 1: GET /v1/models 探测可用模型
    │   Step 2: POST /v1/chat/completions → OpenAI 兼容
    │   Step 3: POST /v1/messages → Anthropic 兼容
    │
    ├─ 检测为 Anthropic → ClaudeSummarizer
    └─ 检测为 OpenAI 兼容 → DeepSeekSummarizer
```

### 优先级 2：Legacy 路径

仅在 `AI_PROVIDER_*` 未配置时作为回退。

## 系统层级

```
调用方（scheduler / oa_digest / ai_chat / 其他）
    │
    ▼
create_summarizer(config) → 工厂函数
    │  AI_PROVIDER_BASE_URL + AI_PROVIDER_API_KEY → auto-detect
    │  回退：legacy path
    ▼
AbstractSummarizer 子类
    │
    ├─ summarize(messages) → SummaryResult（结构化摘要）
    │   ├─ direct：一次调用，max_tokens=8192
    │   ├─ map-reduce：chunk × N → merge
    │   └─ multi-level：chunk → batch merge → final merge
    │
    ├─ _call_chat_api(system, messages) → str（单次对话）
    ├─ _call_chat_api_stream(system, messages) → Iterator[str]（SSE）
    └─ consolidate_memory() → str（群记忆压缩）
```

## 摘要 Pipeline

### 三级策略

```
消息数 → token 估算 → 策略选择

0 条 → 空占位

≤ token_budget → 直接摘要（_summarize_direct）
  一处调用，输出完整 SummaryResult

chunk_count ≤ 5 → Map-Reduce
  Map → _summarize_chunk() × N（提取要点）
  Reduce → _merge_chunk_summaries()（合成最终摘要）

chunk_count > 5 → 多级 Map-Reduce
  Level 1: chunk → 纯文本摘要
  Level 2: batch merge（每 5 个合并一次）
  Level 3: final merge（所有 batch 合成最终摘要）
```

### 结构化输出（SummaryResult）

```python
class SummaryResult(BaseModel):
    summary_text: str        # 完整摘要文本
    topics: list[str]        # 话题列表
    participants: list[ParticipantContribution]

class ParticipantContribution(BaseModel):
    name: str                # 参与者昵称
    contributions: str       # 该参与者的贡献描述
```

- Claude 后端：原生 Pydantic 解析（`client.messages.parse()`）
- DeepSeek 后端：工具调用模拟（`tool_choice="auto"`）

## 流式 AI 对话（SSE）

### 调用链

```
前端用户输入 → POST /api/ai/chat/message {session_id, message}
    │
    ▼
后端创建/恢复 session → _call_chat_api_stream()
    │
    ▼
SSE event stream:
  event: token  → data: {content: "片段"}    逐步追加
  event: done   → data: {token_usage, ...}   完成
  event: error  → data: {message}            错误
```

### Token 管理

| 阈值 | 动作 |
|------|------|
| 初始 context > 70% budget | 预压缩：LLM 总结整个上下文 |
| 对话中 > 90% budget | 自动压缩：保留最近 4 轮，压缩更早历史 |
| 手动点击"压缩历史" | 同自动压缩逻辑 |

### 安全限制

| 限制 | 值 | 说明 |
|------|-----|------|
| `MAX_CONTEXT_CHARS` | 200,000 | 上下文硬上限 |
| `MAX_SINGLE_MSG_CHARS` | 2,000 | 单条消息截断 |
| `MAX_DECOMPRESS_SIZE` | 500,000 | 解压上限 |

### 前端 SSE 实现要点

- **Hoisted State 模式**：父组件持有 messages/streaming/tokenUsage 状态，通过 props 传入 AIChatPanel，关闭 Drawer 不丢失状态
- **SSE local tracking**：局部变量 `currentMessages` 追踪消息数组，避免依赖异步更新的父组件 state 导致 stale closure
- **立即解锁输入框**：收到 `done`/`error` 后立即调用 `reader.cancel()`，不等待 HTTP 流完全关闭

## 对话分组（Session 管理）

- **会话列表页**（ChatTab）：`aiChatSessionsMap = { [talker_id]: {session, messages, ...} }`，每个聊天窗口独立 AI 对话
- **收藏页**（FavoritesTab）：全局单一 AI 会话，关闭 Drawer 不销毁
- 配置态（选择上下文/时间范围）在 Drawer 内完成，不再有外部内联配置面板

## 群记忆（Memory Consolidation）

可选功能，默认关闭（`MEMORY_CONSOLIDATION_ENABLED=false`）。启用后自动将群聊消息整理为"记忆日记"：每 50 条新消息或每 1 小时触发一次 AI 压缩，结果作为长期上下文注入后续摘要 prompt。

## 输出模板

| 模板 | 位置 | 用途 |
|------|------|------|
| `DIGEST_SYSTEM_PROMPT` | `digest.py` | 定时摘要默认 system prompt（话题分类、可行动项、忽略闲聊） |
| `STYLE_PRESETS` | `digest.py` | 三种风格预设（行动项优先/完整复盘/极简速览） |
| `DIGEST_TEMPLATES` | `oa_digest.py` | OA 摘要五种模板 |

## LLM 日志

每次 LLM 调用记录两行日志：
1. `[LLM] <call_type> | <backend>/<model> | <latency> | OK/FAILED | resp: <前80字>`
2. `[LLM-DETAIL] JSON 详情`：完整 prompt、响应、延迟、token 数（API key 已脱敏）

## 关键设计决策

### 1. Map-Reduce 摘要

群聊单次可产生数千条消息。直接塞入一次调用超出 token 限制且输出质量下降（lost-in-the-middle）。chunk-and-merge 让每个分段获得完整注意力再合成。

### 2. 分离后端实现

Claude 和 DeepSeek 的 API 形状根本不同（原生 system 参数 / Pydantic 解析 vs OpenAI SDK + tool calling）。抽象基类隔离共享逻辑，各后端实现 API 细节。

### 3. auto-detect provider

统一 `AI_PROVIDER_BASE_URL` + `AI_PROVIDER_API_KEY` 避免用户需要知道 API 类型。很多用户使用 One API 等代理转发到不同后端，auto-detect 自动适配。

### 4. 花括号转义

所有用户输入（名称、消息）通过 `_esc()` 转义 `{` / `}` 后再 `str.format()`，防止模板注入。

## 代码位置

| 组件 | 文件 |
|------|------|
| AbstractSummarizer | `src/summarize/base.py` |
| ClaudeSummarizer | `src/summarize/claude_backend.py` |
| DeepSeekSummarizer（通用 OpenAI 兼容） | `src/summarize/deepseek_backend.py` |
| Provider 检测 | `src/summarize/provider_detector.py` |
| 工厂函数 | `src/summarize/__init__.py` |
| Prompt 模板 | `src/summarize/prompts.py` + `src/assistant/digest.py` |
| Models | `src/summarize/models.py` |
| LLM 日志 | `src/utils/llm_logger.py` |
| AI 对话后端 | `src/web/ai_chat.py` |
| 前端 AIChatPanel | `ui/src/components/AIChatPanel.jsx` |
| 前端 ChatDrawer | `ui/src/components/ChatDrawer.jsx` |