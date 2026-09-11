# AI 后端与降级

## 1. 功能定位

AI 后端为摘要、对话、Agent 和记忆整理提供统一调用接口。项目支持 Anthropic 和 OpenAI-compatible Provider，也允许用户使用自建或代理服务。

配置入口位于系统配置页面，主要字段为：

- `AI_PROVIDER_BASE_URL`
- `AI_PROVIDER_API_KEY`
- `AI_PROVIDER_TYPE`
- `AI_PROVIDER_MODEL`
- `AI_PROVIDER_EXTRA_BODY`

## 2. Provider 选择

```text
URL + API Key 均存在
  → type=auto：自动探测接口形态
  → type=anthropic：Anthropic 后端
  → 其他：OpenAI-compatible 后端

URL 或 API Key 缺失
  → StubSummarizer
  → 应用继续运行，AI 专用功能返回配置提示
```

“自定义 Provider”表示兼容 OpenAI Chat Completions 形式，并不代表可以直接接入任意协议。

## 3. 调用类型

| 调用 | 场景 |
|---|---|
| `chat` | 通用对话、记忆总结、部分助手工具 |
| `agent_chat` | Agent 工具调用和 ReAct 循环 |
| 流式聊天 | Web AI Chat、朋友圈 AI 总结 |
| 摘要调用 | 群聊摘要、公众号摘要 |
| 长文本调用 | 公众号文章和长内容整理 |

## 4. 未配置 AI 时

未配置 AI 不会阻止应用启动。

### 仍可用

- 消息、收藏、朋友圈浏览与导出；
- 关键词提醒；
- 公众号文章读取和即时提醒；
- script 类型 Skill；
- 不依赖云端 AI 的本地检索和任务功能。

### 不可用

- 群聊/公众号定时摘要；
- 收藏、聊天、朋友圈 AI 对话；
- Agent；
- prompt 类型 Skill；
- AI 记忆整理和上下文压缩。

Stub 的提示包括：

```text
AI 未配置，请先在系统配置中设置 AI 提供商。
AI 未配置，无法调用摘要接口
AI 未配置，无法使用 Agent 功能
```

## 5. 错误分类

Provider 检测会区分：

- URL 或 API Key 未填写；
- 凭据无效；
- 地址无法连接；
- Provider 返回不兼容响应；
- 模型或上下文超限。

AI 调用失败时，摘要任务会进入失败状态；不会把错误当成“没有新内容”。

## 6. SSE 对话

```text
POST /api/ai/chat/start
  → 读取本地上下文并创建 session

POST /api/ai/chat/message
  → SSE token 事件持续返回内容
  → done 事件表示完成
  → error 事件表示失败
```

聊天上下文有字符上限和压缩机制。关闭对话面板不会自动删除 session，用户选择“开启新对话”时才会销毁旧 session。

## 7. 当前已知体验差异

未配置 AI 时，部分 AI 专用接口仍可能先返回会话或普通回复，随后才在真正调用阶段显示未配置提示；Dashboard 也可能显示“未响应”而不是“未配置”。这属于状态表达问题，不影响消息读取、提醒和导出等非 AI 主链。

## 8. 代码位置

- Provider 工厂：`src/summarize/__init__.py`
- Stub：`src/summarize/stub_backend.py`
- Provider 检测：`src/summarize/provider_detector.py`
- AI 异常：`src/summarize/errors.py`
- Web AI Chat：`src/web/ai_chat.py`
- 前端 AI Chat：`ui/src/components/AIChatPanel.jsx`
