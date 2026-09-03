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
调用方（scheduler / oa_digest / ai_chat / agent / skill / 其他）
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
    ├─ agent_chat(system, messages, tools) → (content, tool_calls, reasoning)
    │   Agent 的 ReAct 循环调用；OpenAI 兼容实现提取 reasoning_content
    │   （thinking 模式产物，必须保留在对话历史中，否则上游 API 报错）
    │
    ├─ _call_chat_api(system, messages) → str（单次对话）
    ├─ _call_chat_api_stream(system, messages, extra_body) → Iterator[str]（SSE）
    ├─ _call_long_api(system, messages, max_tokens, temperature) → str（长文本）
    │   供 OA 摘要等使用；thinking 模式吃光 token 时自动禁用并重试
    │
    └─ consolidate_memory() → str（群记忆压缩）
```

## DeepSeek 实现细节（OpenAI 兼容后端）

| 项目 | 值 |
|------|-----|
| 默认模型 | `deepseek-v4-pro`（旗舰，1M 上下文） |
| 快速模型 | `deepseek-v4-flash`（快/便宜，1M 上下文） |
| token 预算 | 900K（1M 上下文留安全边际） |
| 结构化输出 | 工具调用模拟（`STORE_SUMMARY_TOOL` + `tool_choice="auto"`） |

- **extra_body 语义**：provider 附加参数（如 `{"thinking": {"type": "disabled"}}`）必须经 OpenAI SDK 的 `extra_body` 参数传递，不能平铺进顶层 kwargs，否则报 `unexpected keyword argument`。调用方可通过 `_call_chat_api_stream(..., extra_body=...)` 按需传入。
- **thinking 模式**：DeepSeek 开启思考时响应带 `reasoning_content`；若思考吃光 max_tokens 导致 content 为空，`_call_long_api` 自动禁用 thinking 并加倍 max_tokens 重试。
- **空响应降级**：各调用方法在 content 为空时返回 `"..."` 并记 warning，不抛异常中断链路。

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

## LLM 异常体系（`src/summarize/errors.py`）

中转 API（new-api 系）在**上下文超限**时返回 HTTP **200** + `{"choices": null, "base_resp": {"status_code": 2013, "status_msg": "invalid params, context window exceeds limit"}}`。因为状态码是 200，OpenAI SDK 不抛任何 HTTP 错误，`response.choices[0]` 直接炸成 `TypeError: 'NoneType' object is not subscriptable`，最终变成"摘要生成失败: 'NoneType' object is not subscriptable"推到任务中心 —— 用户完全看不出是超限。

```
LLMError(RuntimeError)
└── LLMResponseError            响应体不可用（choices 为 null / content 为空）
    └── LLMContextOverflowError 输入超出上下文窗口 → 调用方应降级，不是重试
```

- **基类选 `RuntimeError` 而非 `Exception`**：既有的 `except RuntimeError` 兜底（`consolidate_memory`、`AbstractSummarizer.chat`）继续生效。
- `LLMResponseError` 带 `prompt_tokens` / `status_code` / `status_msg` / `raw`。**超限时 `usage.prompt_tokens` 仍然返回**，是判断"超了多少"的唯一依据，必须带进错误串。
- `OpenAICompatSummarizer._extract_choice()` 与 `ClaudeSummarizer._extract_text()` 是统一取值入口，替代裸的 `response.choices[0]` / `response.content[0]`。
- **超限不能被 thinking guard 的宽泛 `except Exception` 吞掉**：两个降级通道都在 `except Exception` 之前单独 `except LLMContextOverflowError: raise`。换通道不可能改善超限（输入相同、`max_tokens` 反而更大），而吞掉它会退化成空 content → `"..."`（真值）→ 任务被误判为成功、正文是一串点。
- `retry_exceptions` 不含 `LLMError`，超限不会被 backoff 无意义重试 3 次。

真实 provider 实测（MiniMax-M2.7-highspeed）：738,882 字符 → 4.1s 抛 `LLMContextOverflowError`，`prompt_tokens=279826`、`status_code=2013`，错误串为 `[DIGEST-API] LLM 返回空 choices（base_resp=2013: invalid params, context window exceeds limit），model=...，prompt_tokens=279826`。

## Per-request 超时

client 级 httpx 超时是 `Timeout(60.0, connect=10.0)`（构造时写死，AI 对话路径依赖它快速失败）。但摘要延迟由**输出长度**决定而非输入长度 —— 实测固定开销约 18s，之后约 14ms/字；6 个会话打包、输出 1542 字时耗时 40.6s，已逼近 60s。

因此摘要路径用 openai SDK 支持的 per-request `timeout=` 单独放宽，**不改全局 client**：

| 接口 | timeout 形参 | 说明 |
|------|-------------|------|
| `_call_digest_api` | ✅ | 群摘要；scheduler 传 `DIGEST_LLM_TIMEOUT_SEC`（env，默认 180） |
| `_call_long_api` | ✅ | 公众号摘要等长文本 |
| `_call_chat_api` | ❌ | 对话与记忆更新，输出短，保持 client 级 60s |
| `_call_chat_api_stream` | ❌ | 流式的 read timeout 是相邻 chunk 间隔，语义本就不同 |

`AbstractSummarizer._request_timeout(seconds)` 返回 `httpx.Timeout(seconds, connect=10.0)` —— **必须传 `httpx.Timeout` 实例而不是 float**，否则 connect 超时也会跟着变成 180s，"连不上"要等满 3 分钟才失败。thinking guard 的三个通道（主 / thinking-disabled / max_tokens 加倍）都会带上该 timeout。

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