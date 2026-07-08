# 整体架构

## 一句话说明

本地运行的微信消息助手：从微信本地数据库读取消息与文章，AI 生成结构化摘要和即时提醒，通过 iLink 通道推送到微信私聊。不主动操控微信窗口、不接入 Web API，数据全程不出本机。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端语言 | Python 3.13 |
| 前端框架 | React 19 + Vite 8 + Tailwind 4 |
| 桌面容器 | PyWebView（WebView2） |
| AI 后端 | DeepSeek / Claude / 任意 OpenAI 兼容 API（统一 provider 自动检测） |
| HTTP 服务 | 纯 Python `http.server.ThreadingHTTPServer`，零外部依赖 |
| 实时通信 | WebSocket（状态/事件推送）+ SSE（AI 流式输出） |
| 调度 | 自定义 daemon 线程（群聊摘要 / 公众号摘要） |
| 持久化 | SQLite（消息 / 通知队列）+ JSON（助手配置） |
| 打包 | PyInstaller → `wx-assist.exe` |

## 目录结构

```
src/
├── bot.py                    # 组件编排、生命周期
├── config.py                 # BotConfig dataclass，.env 加载
├── router.py                 # 消息路由（持久化 + iLink DM → Agent）
├── main.py                   # CLI 入口
├── desktop.py                # PyWebView 桌面入口
├── admin.py                  # 管理员命令
├── nickname.py               # 昵称服务
│
├── agent/                    # Agent 子系统
│   ├── engine.py             # ReAct Loop 引擎（同步）+ 记忆系统
│   ├── tools.py              # 工具定义（与 MCP 共享同一注册表）
│   ├── registry.py           # ToolRegistry 中心注册表
│   ├── mcp_server.py         # MCP Server（标准 JSON-RPC 2.0，端口 17328）
│   └── __init__.py           # 导出 AgentEngine / ToolExecutor / ToolRegistry
│
├── assistant/                # 助手子系统
│   ├── config.py             # AssistantConfig + 所有 dataclass
│   ├── scheduler.py          # DigestScheduler — 群聊/公众号定时摘要 daemon
│   ├── digest.py             # prompt 构建、消息过滤、记忆更新、媒体占位
│   ├── alert.py              # 关键词即时提醒引擎
│   ├── oa_digest.py          # 公众号摘要生成 + 模板
│   ├── oa_monitor.py         # 公众号新文章即时提醒后台轮询
│   ├── oa_groups.py          # 公众号分组 CRUD
│   ├── oa_parser.py          # 文章解析
│   ├── oa_reader.py          # 全文抓取
│   ├── outbox.py             # SQLite 通知队列
│   └── task_center.py        # 任务中心 SQLite 持久化
│
├── summarize/                # AI 后端
│   ├── __init__.py           # create_summarizer() 工厂 + provider 检测
│   ├── base.py               # AbstractSummarizer（chunk / merge / map-reduce）
│   ├── claude_backend.py     # Anthropic 实现
│   ├── deepseek_backend.py   # OpenAI 兼容实现（支持任意兼容端点）
│   ├── provider_detector.py  # 自动检测 API 类型
│   ├── models.py             # SummaryResult Pydantic model
│   └── prompts.py            # prompt 模板
│
├── web/                      # Web UI 服务
│   ├── server.py             # HTTP + WebSocket 服务器
│   ├── api_handlers.py       # 所有 REST API 路由处理
│   └── ai_chat.py            # AI 对话会话管理 + SSE 流式
│
├── wechat/                   # 微信集成
│   ├── wcdb_backend.py       # 本地数据后端（轮询 + 消息标准化）
│   ├── wcdb_client.py        # 本地数据接口封装
│   ├── extract_key.py        # 连接凭证获取
│   ├── ilink_push.py         # iLink Bot 推送通道
│   ├── ilink_receiver.py     # iLink 消息轮询接收（Agent 入口）
│   ├── image_decrypt.py      # 图片处理（WASM 子进程）
│   ├── voice_decode.py       # 语音转码（SILK → WAV）
│   ├── sns_client.py         # 朋友圈客户端
│   └── helpers.py            # 消息去重 / 类型映射
│
├── db/                       # SQLite 持久化
│   ├── schema.py             # 表定义
│   └── store.py              # MessageStore CRUD
│
├── memory/                   # 群记忆
│   └── consolidator.py       # 定期将聊天历史压缩为群记忆
│
├── guard/                    # 不良内容检测
├── scheduler/                # 通用定时任务
└── utils/                    # 日志、操作追踪等工具
```

## 启动流程

### 桌面模式（正式入口）

```
desktop.py
  1. 修正 CWD（PyInstaller 兼容）
  2. 检查 onboarding（.env 是否存在）
  3. start_web_server() → daemon 线程，端口 17327
  4. pywebview.create_window("wx-assist", "http://127.0.0.1:17327")
  5. 用户在 UI 完成 onboarding → POST /api/start
  6. Bot.run() 在后台线程中初始化所有组件
```

### Bot.run() 初始化顺序

```
1. SQLite 初始化 + MessageStore
2. Summarizer + NicknameService + AdminHandler
3. MessageRouter（组合以上组件）
4. WeChat Backend（WcdbBackend）
5. 健康监控 daemon（30s 心跳）
6. Assistant 子系统（如果 assistant_enabled）：
   - AlertEngine（关键词提醒）
   - DigestScheduler（群聊 + 公众号定时摘要）
   - OAMonitorEngine（公众号即时提醒）
   - TaskCenter（任务中心）
   - Outbox（通知队列）
7. AI Agent：
   - ToolExecutor（注册 9 个工具 + confirm_action）
   - AgentEngine（ReAct Loop + 记忆系统）
   - MCP Server（端口 17328，标准 JSON-RPC 2.0）
   - 注入 Router（iLink DM → Agent）
8. iLink Receiver 自动启动（绑定后轮询消息）
9. backend.start(callback) — 阻塞式轮询群消息
```

### 关键约束

Web server 是 daemon 线程，主进程退出后服务消失。源码模式必须保持主线程存活：

```powershell
$env:PYTHONPATH='.'
python -c "from src.web.server import start_web_server; import time; t = start_web_server(); [time.sleep(1) for _ in iter(int, 1)]"
```

启动后通过 `POST /api/start` 初始化 bot 后端。

## Agent 系统

Agent 通过 iLink 私聊（DM）与用户交互。用户发一条微信消息给机器人，Agent 自主理解意图、调用工具、返回结果。

### 设计原则

| 原则 | 说明 |
|------|------|
| **只走 iLink DM** | 不做群聊 @ 入口 |
| **Agent = LLM + 工具** | 无意图分类器，LLM 自行判断是否需要工具 |
| **全同步** | 无 async/await，在 iLink receiver 线程中同步执行 |
| **写操作硬拦截** | `requires_confirm` 在引擎层强制，非 LLM 软约束 |
| **工具注册一次，两端可见** | ToolRegistry 同时供给 Agent 和 MCP |

### 消息流（iLink DM）

```
用户微信发送私聊消息
    │ POST ilink/bot/getupdates (长轮询 30s)
    ▼
ILinkReceiver._poll_loop (daemon 线程, 3s 间隔)
    │ fetch_updates → _parse_message → standardize_for_router
    ▼
MessageRouter.handle(msg)
    │ 1. insert_message (SQLite 持久化)
    │ 2. chat_id 以 "ilink_" 开头 → _handle_dm
    ▼
AgentEngine.run(user_message)
    │ 加载短期记忆 (_history) + 长期记忆 (agent_memory 表)
    │ 注入 system prompt + tool descriptions
    ▼
ReAct Loop (最多 8 步)
    │ LLM (system + messages + tools)
    │ → (content, tool_calls)
    │   ├─ 无 tool_calls → 返回回复
    │   ├─ confirm_action → 拦截，存 _pending_confirm，返回确认提示
    │   ├─ 写操作 + 已确认 → 执行工具
    │   └─ 写操作 + 未确认 → 拒绝，提示先调用 confirm_action
    ▼
ILinkPush.send_message(reply) → 用户微信收到回复
```

### 工具列表

所有工具定义在 `src/agent/tools.py`，通过 `ToolRegistry.register()` 注册：

| 工具名 | 类型 | 需确认 | 用途 |
|--------|:----:|:------:|------|
| `get_status` | 读 | ❌ | 查看机器人运行状态 |
| `list_digests` | 读 | ❌ | 查看已配置的定时摘要 |
| `list_alerts` | 读 | ❌ | 查看已配置的关键词预警 |
| `list_oa_groups` | 读 | ❌ | 查看公众号监控分组 |
| `list_tasks` | 读 | ❌ | 查看任务中心记录 |
| `run_digest` | 写 | ✅ | 手动触发群聊摘要 |
| `run_oa_digest` | 写 | ✅ | 手动触发公众号摘要 |
| `add_alert` | 写 | ✅ | 添加关键词预警 |
| `add_digest` | 写 | ✅ | 配置定时摘要 |

新增工具只需一行 `r.register(...)`，Agent 和 MCP Server 自动可见。

### confirm_action 状态机

写操作工具标记 `requires_confirm=True`，引擎强制拦截：

```
用户: "帮我盯着项目群的 bug"
  → LLM 调 confirm_action({action:"添加关键词预警"})
  → 引擎拦截，存 _pending_confirm
  → 返回 "⚠️ 需要确认：添加关键词预警\n\n回复确定执行，回复取消放弃。"
用户: "确定"
  → 引擎注入 "用户已确认" → LLM 调 add_alert → 执行
```

### 记忆系统

| 层级 | 存储 | 范围 |
|------|------|------|
| 短期记忆 | 内存 `_history` 列表 | 最近 10 轮对话 |
| 长期记忆 | SQLite `agent_memory` 表 | 跨会话持久 |

短期达到 10 轮后，LLM 自动总结为一条长期记忆存入 DB，下次对话注入 system prompt。

### 欢迎语

首条 DM 触发一次，工具列表从 ToolRegistry 动态生成，新增工具欢迎语自动更新。

## MCP Server (Model Context Protocol)

标准 MCP 实现，Streamable HTTP 传输，兼容任何 MCP 客户端。

| 地址 | `http://127.0.0.1:17328` |
|------|-------------------------|
| 协议 | JSON-RPC 2.0 |
| 传输 | Streamable HTTP (POST /) |
| 端点 | `initialize` / `tools/list` / `tools/call` / `ping` |
| 工具 | 与 Agent 共用同一 ToolRegistry，写操作工具不可用（无法处理微信确认） |

### 调用示例

请求：
```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
```

响应：
```json
{"jsonrpc": "2.0", "id": 1, "result": {
  "tools": [{"name": "get_status", "description": "...", "inputSchema": {}}]
}}
```

## 消息流

### 群消息（WCDB 轮询）

```
微信收到新消息
    │
    ▼
WcdbBackend 轮询读取 session.db
    │  去重 + 排除自身 + 标准化
    ▼
MessageRouter.handle(msg)
    │  持久化到 SQLite
    │  可选触发群记忆整合
    ▼
Bot._wrapped_callback(msg)
    │
    └── assistant_alert.check(msg)   ← 关键词即时提醒
            │ 命中 → Outbox + 可选 iLink 推送
            ▼
        通知队列 / 微信私聊
```

### iLink DM（Agent 路径）

```
用户微信发私聊
    │
    ▼
ILinkReceiver (3s 轮询 getupdates)
    │  解析 → 标准化 → router.handle
    ▼
MessageRouter → _handle_dm → AgentEngine.run()
    │  ReAct Loop (8 步上限)
    ▼
ILinkPush → 用户收到回复
```

### 定时任务

```
DigestScheduler daemon 线程
    │  60s 轮询，cron 匹配触发
    ▼
_generate_digest(dg)
    │  拉取消息 → 过滤 → AI 摘要 → 记忆更新
    ▼
Outbox + 可选 iLink 推送
```

## 前端结构

React 单页应用，左侧固定导航 + 右侧内容区：

| Tab | 组件 | 功能 |
|-----|------|------|
| 运行状态 | Dashboard | 服务状态、系统健康、即时提醒/定时任务/Agent 总览 |
| 系统配置 | ConfigPanel | AI 后端、数据路径、消息推送、功能开关、AI 调试台 |
| 群聊助手 | AssistantPanel | 定时摘要、群档案、关键词提醒、任务中心、通知中心 |
| 会话管理 | ChatTab | 会话列表、消息浏览、图片/语音、导出 |
| 收藏助手 | FavoritesTab | 收藏浏览、AI 对话、导出 |
| 朋友圈助手 | MomentsTab | 朋友圈浏览、图片/视频、HTML 归档 |
| 公众号助手 | OATab | 分组、文章列表、摘要生成、即时提醒 |
| 运行日志 | LogViewer | 实时日志查看 |

## 线程安全

| 组件 | 保护机制 | 说明 |
|------|----------|------|
| WCDB DLL 调用 | `threading.Lock` + 15s 超时 | 串行化所有 ctypes 调用 |
| 去重集合 | `_lock` | 保护所有 mutation |
| WebSocket 广播 | snapshot-then-send | 广播前快照订阅者列表 |
| .env 写入 | 原子写入 + 文件锁 | tmp + `os.replace()` |
| Agent `_pending_confirm` | 实例级，无竞争 | 单 iLink 单用户，不会并发 |

## 配置体系

- **BotConfig**（`src/config.py`）：从 `.env` 加载，控制 AI 后端、数据路径等基础项。
- **AssistantConfig**（`src/assistant/config.py`）：JSON 持久化到 `data/assistant_config.json`，控制摘要群、提醒群、公众号分组等。全部通过前端 UI 管理，无需手编。
- **iLink 凭据**：`data/ilink_account.json`，扫码绑定时动态生成。

## 依赖关系

后端核心依赖：`anthropic`、`openai`、`pydantic`、`pywin32`、`pywebview`、`zstandard`、`pycryptodome`、`Pillow`、`psutil`

前端核心依赖：`react` 19、`framer-motion`、`@phosphor-icons/react`、`qrcode.react`、`tailwindcss` 4

## 模块文档索引

| 模块 | 文档 | 说明 |
|------|------|------|
| 群聊摘要 | [modules/group-digest.md](modules/group-digest.md) | 定时摘要 + 群档案 + 推送 |
| 关键词提醒 | [modules/keyword-alert.md](modules/keyword-alert.md) | 即时提醒 + 防误触 + 推送 |
| 公众号助手 | [modules/oa-assistant.md](modules/oa-assistant.md) | 摘要 + 即时提醒 |
| AI 后端 | [modules/ai-backend.md](modules/ai-backend.md) | provider 检测 / 摘要 / 流式对话 |
| 微信推送 | [modules/ilink-push.md](modules/ilink-push.md) | iLink Bot 推送通道 |
| 调度器 | [modules/scheduler.md](modules/scheduler.md) | cron 约定与调度设计 |
| 通知队列 | [modules/notification-outbox.md](modules/notification-outbox.md) | 统一通知模型 |
| Agent 系统 | — | 本文档已涵盖 Agent 架构设计 |
| MCP Server | — | 本文档已涵盖 MCP 协议和端口 |