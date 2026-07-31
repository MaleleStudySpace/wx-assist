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
| Agent | ReAct 循环引擎（工具调用 + 写操作确认 + 双记忆） |
| HTTP 服务 | 纯 Python `http.server.ThreadingHTTPServer`，零外部依赖 |
| 实时通信 | WebSocket（状态/事件推送）+ SSE（AI 流式输出） |
| 调度 | CronScheduler（通用 skill 定时）+ DigestScheduler（群聊/公众号摘要） |
| Skill | YAML frontmatter 定义的能力单元（script 子进程 / AI 生成） |
| MCP | 双向：client（连外部 server）+ server（暴露本地工具，端口 17328） |
| 持久化 | SQLite（消息 / 通知队列 / 任务中心 / 记忆）+ JSON（助手配置 / cron 任务 / MCP 配置） |
| 语义检索 | ChromaDB（向量库）+ fastembed / ONNX Runtime（本地向量化） |
| 打包 | PyInstaller → `wx-assist.exe` |

## 目录结构

```
src/
├── bot.py                    # 组件编排、生命周期
├── config.py                 # BotConfig dataclass，.env 加载
├── router.py                 # 消息路由（持久化 + iLink DM → Agent + RAG 增量索引）
├── main.py                   # CLI 入口
├── desktop.py                # PyWebView 桌面入口
├── admin.py                  # 管理员命令
├── nickname.py               # 昵称服务
│
├── agent/                    # Agent 子系统
│   ├── engine.py             # AgentEngine — ReAct Loop 引擎（同步）+ 双记忆
│   ├── tools.py              # ToolExecutor — 25 个内置工具
│   ├── registry.py           # ToolRegistry 中心注册表
│   ├── mcp_server.py         # 反向 MCP Server（JSON-RPC 2.0，端口 17328）
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
│   ├── rag/                  # RAG 语义检索子系统
│   │   ├── engine.py         # RAGEngine（ingest / search / build_context）
│   │   ├── embedder.py       # fastembed + ONNX Runtime 本地向量化
│   │   ├── vector_store.py   # ChromaDB 向量存储封装
│   │   ├── chunking.py       # 文本分块策略（滑窗 / 单条）
│   │   └── reranker.py       # 检索结果重排序（当前占位）
│   ├── rag_types.py          # 纯 dataclass 定义（零外部依赖）
│   └── task_center.py        # 任务中心 SQLite 持久化
│
├── mcp/                      # MCP Client（连外部 MCP server）
│   ├── client.py             # 传输层：StdioClient / HttpClient（纯 stdlib + requests）
│   ├── manager.py            # 生命周期：心跳 / 降级 / 恢复 / 热管理
│   ├── tool_registry.py      # MCP 工具 ↔ LLM schema 转换与分发
│   └── config_schema.py      # data/user_mcp.json 配置校验
│
├── skill/                    # Skill 体系
│   └── engine.py             # SkillEngine — 加载 / 执行 / 创建 skill
│
├── summarize/                # AI 后端
│   ├── __init__.py           # create_summarizer() 工厂 + provider 检测
│   ├── base.py               # AbstractSummarizer（摘要 / 对话 / agent_chat / 记忆压缩）
│   ├── claude_backend.py     # Anthropic 实现
│   ├── deepseek_backend.py   # OpenAI 兼容实现（支持任意兼容端点）
│   ├── provider_detector.py  # 自动检测 API 类型
│   ├── models.py             # SummaryResult Pydantic model
│   └── prompts.py            # prompt 模板
│
├── web/                      # Web UI 服务
│   ├── server.py             # HTTP + WebSocket 服务器 + REST 路由
│   ├── api_handlers.py       # 业务 API 处理
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
│   ├── wcdb_sns_reader.py    # 朋友圈数据读取
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
├── scheduler/                # 通用定时任务引擎
│   └── cron_scheduler.py     # CronScheduler — 任意 skill 的 cron 调度
└── utils/                    # 日志、cron 解析、操作追踪等工具
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
   - ContentCache（OA/SNS/Fav 数据本地缓存）
7. AI Agent + Skill + MCP：
   - ToolExecutor（25 个内置工具）
   - MCPServerManager（读 data/user_mcp.json，逐个握手注入工具表）
   - MCPToolRegistry + ProxyRegistry 包裹（LLM 无感看到 MCP 工具）
   - AgentEngine（ReAct Loop + 记忆系统）
   - SkillEngine（加载 data/skills/ 下 skill）
   - CronScheduler（通用 skill 定时任务）
   - 反向 MCP Server（端口 17328，标准 JSON-RPC 2.0）
   - 注入 Router（iLink DM → Agent）
8. RAG 语义检索（如果 AI 可用）：
   - FastEmbedder（本地 ONNX 模型，bge-small-zh-v1.5）
   - VectorStore（ChromaDB 初始化）
   - RAGEngine（ingest + search + build_context）
   - 冷启动索引（后台线程回溯 30 天聊天记录）
   - ContentCache 增量索引定时器（OA 60s / SNS 5min / Fav 10min）
   - 重启时 cache 有数据则跳过全量 sync，增量索引仅处理新增内容
9. iLink Receiver 自动启动（绑定后轮询消息）
10. backend.start(callback) — 阻塞式轮询群消息

清理序列（finally 块）：
  MCP client shutdown 排第一 → OA monitor → 摘要调度器 → CronScheduler
  → 健康监控 → iLink receiver → 数据库关闭
```

### 关键约束

Web server 是 daemon 线程，主进程退出后服务消失。源码模式必须保持主线程存活：

```powershell
$env:PYTHONPATH='.'
python -c "from src.web.server import start_web_server; import time; t = start_web_server(); [time.sleep(1) for _ in iter(int, 1)]"
```

启动后通过 `POST /api/start` 初始化 bot 后端。

## Agent 系统

详见 [modules/agent.md](modules/agent.md)。摘要：

### 设计原则

| 原则 | 说明 |
|------|------|
| **入口多样** | iLink 私聊（主）、Web 测试、定时任务（AI 型 skill）、反向 MCP |
| **Agent = LLM + 工具** | 无意图分类器，LLM 自行判断是否需要工具 |
| **全同步** | 无 async/await，在 iLink receiver 线程中同步执行 |
| **写操作硬拦截** | `requires_confirm` 在引擎层强制，非 LLM 软约束 |
| **工具注册一次，多处可见** | ToolRegistry 同时供给 Agent、反向 MCP Server、欢迎语 |

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

### 工具清单（25 个）

所有工具定义在 `src/agent/tools.py`，通过 `ToolRegistry.register()` 注册：

**基础工具（18）**：`get_status`、`list_digests`、`list_alerts`、`list_oa_groups`、`list_oa_monitors`、`list_tasks`、`run_digest`、`run_oa_digest`、`search_oa_accounts`、`add_alert`、`add_digest`、`add_oa_scheduled_digest`、`add_oa_monitor`、`confirm_action`、`search_chat_history`、`search_oa_articles`、`search_moments`、`search_favorites`

**定时任务工具（4）**：`create_cron`、`delete_cron`、`list_crons`、`run_cron`（`set_cron_scheduler()` 注入后注册）

**Skill 工具（3）**：`list_skills`、`execute_skill`、`create_skill`（`set_skill_engine()` 注入后注册）

写操作（`add_*`、`run_*`、`create_cron`、`delete_cron`、`create_skill`）标记 `requires_confirm=True`，引擎层强制拦截。新增工具只需一行 `r.register(...)`，Agent、反向 MCP Server、欢迎语自动可见。

### confirm_action 状态机

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

短期达到 10 轮后，LLM 自动总结为一条长期记忆存入 DB，下次对话注入 system prompt（最多 3 条）。

## Skill 体系

详见 [modules/skill.md](modules/skill.md)。摘要：

- **定义**：`data/skills/{name}/SKILL.md`（YAML frontmatter），两种类型——`script`（子进程执行脚本）和 `ai`（注入 Agent 一次性生成）
- **统一执行**：`SkillEngine.execute(name, args)` 分派，返回值 `[SILENT]` 表示无新内容
- **使用场景**：定时任务（CronScheduler）+ AI 助手（`list_skills` / `execute_skill` / `create_skill` 工具）
- **内置示例**：`weather`（script，天气查询）+ `skill-designer`（ai，技能设计助手），`POST /api/skills?sample=1` 一键生成

## 定时任务

### 通用 skill 定时（CronScheduler，新）

详见 [modules/cron-scheduler.md](modules/cron-scheduler.md)。

```
CronScheduler daemon 线程（60s tick + cron 匹配 + 120s 防重触）
    │
    ▼
_execute_and_push(job)
    │  TaskCenter 记账 → SkillEngine.execute(skill, args)
    │  [SILENT] → 跳过推送；报错 → 记 error_count + 推送错误
    ▼
Outbox (notif_type="cron") + 可选 iLink 推送
```

### 摘要定时（DigestScheduler，旧）

```
DigestScheduler daemon 线程
    │  60s 轮询，cron 或 HH:MM 匹配触发
    ▼
_generate_digest(dg)
    │  拉取消息 → 过滤 → AI 摘要 → 记忆更新
    ▼
Outbox + 可选 iLink 推送
```

两者并行运行：CronScheduler 面向任意 skill（任务存 `data/cron_jobs.json`），DigestScheduler 面向群聊/公众号摘要（配置存 `data/assistant_config.json`）。

## RAG 语义检索

RAG（Retrieval-Augmented Generation）对聊天记录、公众号文章、朋友圈、收藏内容自动建立语义索引，在 AI 对话和 Agent 检索中精准召回最相关内容，不再依赖关键词"猜"。

### 架构

```
┌───────────────────────────────────────────────────────────┐
│                数据源（4 类，增量索引）                      │
│  聊天记录 (router 实时增量) · 公众号文章 (oa_cache)          │
│  朋友圈 (sns_cache) · 收藏 (fav_cache)                     │
└───────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────┐
│                 RAGEngine (src/assistant/rag/)             │
│  ingest / ingest_one（滑窗分块 → embed → 入库）            │
│  search（embed → 粗搜 top_k*2 → 阈值过滤 → rerank 取 top_n）│
│  build_context（格式化检索结果文本）                        │
└───────────────────────────────────────────────────────────┘
                              │
                  ┌───────────┴───────────┐
                  ▼                       ▼
┌──────────────────────┐  ┌──────────────────────┐
│  Agent 工具调用      │  │  AI Chat 上下文注入   │
│  search_chat_history │  │  会话启动自动拉取     │
│  search_oa_articles  │  │  相关文章增强回答     │
│  search_moments      │  │                      │
│  search_favorites    │  │                      │
└──────────────────────┘  └──────────────────────┘
```

### 向量化

| 组件 | 说明 |
|------|------|
| 模型 | BAAI/bge-small-zh-v1.5（中文优化，384 维） |
| 推理 | ONNX Runtime（本地 CPU，无 GPU 需求） |
| 模型文件 | 本地 `models/` 目录加载（源码 = 项目根，EXE = 打包目录），不联网 |
| 存储 | ChromaDB `data/chroma`，cosine 距离，metadata 含时间戳用于过期清理 |

### 索引策略

- **4 类数据源**：聊天记录（Router 每条消息持久化后实时 `ingest_one`）；公众号文章 / 朋友圈 / 收藏（ContentCache 增量索引定时器，游标持久化 `data/last_indexed.json`）
- **冷启动**：后台线程回溯 30 天聊天记录，每批 1000 条，游标持久化 `data/rag_state.json`
- **过期清理**：超过 60 天的 chunk 每小时自动压缩清理
- **加密内容过滤**：微信 4.x 非文本消息的密文 hex（`28b52ffd` 开头且长于 100）不索引

### 检索流程

```
用户提问 "xxx"
    │
    ▼
RAGEngine.search(q, top_k=5)
    │
    ├─ 1. embed(query) → 向量（本地 ONNX 推理）
    ├─ 2. ChromaDB 粗搜 top_k*2 候选（默认 20）
    ├─ 3. 相似度阈值 0.4 过滤
    ├─ 4. rerank 取最终 top_n（当前为 NoopReranker 占位，直接取前 N）
    └─ 5. build_context → 格式化文本回填
```

### 数据源覆盖

| 数据源 | 索引途径 | 覆盖内容 | 分块 |
|--------|----------|----------|------|
| 聊天记录 | Router 实时增量 + 冷启动 | 群聊/私聊历史消息 | 滑窗（3 条窗口） |
| 公众号文章 | ContentCache 增量 | 标题 + 摘要 + 全文前 2000 字 | 滑窗 |
| 朋友圈 | ContentCache 增量 | 昵称 + 正文 | 单条 |
| 收藏 | ContentCache 增量 | 正文 | 单条 |

## MCP（双向）

### MCP Client（连外部 server）

详见 [modules/mcp-client.md](modules/mcp-client.md)。wx-assist 作为 client 连接用户配置的外部 MCP server，把对方工具注入 LLM function calling，实现插件化扩展：

```
data/user_mcp.json（用户手编配置）
    │
    ▼
MCPServerManager.init_from_config → 逐个握手（initialize + list_tools）
    │  工具命名 {server}__{tool}，冲突后者跳过
    ▼
ProxyRegistry 包裹 tool_executor.registry → Agent 无感看到 MCP 工具
    │
    ▼
LLM 调 "server__tool" → manager.invoke → client.call_tool → 回填 Observation
```

- **传输**：stdio（本地子进程，线程模型 + 串行锁） / http（远程端点，兼容 Streamable HTTP 的 SSE 响应）
- **高可用**：5s 心跳，连续 3 次失败降级摘除工具表，恢复后自动重新注入
- **安全**：子进程不继承敏感环境变量；写操作确认由本地 `confirm_action` 机制兜底
- **状态**：WebSocket 广播 `mcp_servers` 字段，前端 MCPTab 展示与操作

### 反向 MCP Server（对外暴露）

标准 MCP 实现，Streamable HTTP 传输，兼容任何 MCP 客户端。

| 地址 | `http://127.0.0.1:17328` |
|------|-------------------------|
| 协议 | JSON-RPC 2.0 |
| 传输 | Streamable HTTP (POST /) |
| 端点 | `initialize` / `tools/list` / `tools/call` / `ping` |
| 工具 | 与 Agent 共用同一 ToolRegistry（不含 MCP 注入工具），写操作工具不可用 |

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

### 群消息（本地数据轮询）

```
微信收到新消息
    │
    ▼
本地数据后端轮询读取
    │  去重 + 排除自身 + 标准化
    ▼
MessageRouter.handle(msg)
    │  持久化到 SQLite
    │  可选触发群记忆整合（50 条或 1 小时）
    │  可选触发 RAG 实时索引
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

## 前端结构

React 单页应用，左侧固定导航 + 右侧内容区：

| Tab | 组件 | 功能 |
|-----|------|------|
| 运行状态 | Dashboard | 服务状态、系统健康、即时提醒/定时任务/Agent 总览 |
| 系统配置 | ConfigPanel | AI 后端、数据路径、消息推送、功能开关、AI 调试台 |
| 群聊助手 | AssistantPanel | 定时摘要、群档案（含群记忆）、关键词提醒、通知中心 |
| 定时任务 | SchedulerPanel | 定时任务 CRUD + Skill 库 + 执行历史（3 个 section） |
| 会话管理 | ChatTab | 会话列表、消息浏览、图片/语音、AI 对话 |
| 收藏助手 | FavoritesTab | 收藏浏览、AI 对话、导出 |
| 朋友圈助手 | MomentsTab | 朋友圈浏览、图片/视频、AI 快速总结、导出 |
| 公众号助手 | OATab | 分组、文章列表、摘要生成、即时提醒 |
| MCP 管理 | MCPTab | 外部 MCP server 状态、工具开关、添加/编辑 |
| 运行日志 | LogViewer | 实时日志查看 |
| 任务中心 | TaskCenter（抽屉） | 全局任务生命周期聚合（群聊/公众号/定时/缓存） |

## 线程安全

| 组件 | 保护机制 | 说明 |
|------|----------|------|
| 本地数据接口调用 | `threading.Lock` + 15s 超时 | 串行化所有 ctypes 调用 |
| 去重集合 | `_lock` | 保护所有 mutation |
| WebSocket 广播 | snapshot-then-send | 广播前快照订阅者列表 |
| .env 写入 | 原子写入 + 文件锁 | tmp + `os.replace()` |
| Agent `_pending_confirm` | 实例级，无竞争 | 单 iLink 单用户，不会并发 |
| RAG 增量索引 | `threading.Lock` 单锁 + `_pending` 防重入 | 同源串行，异源排队，`finally` 清锁 |
| MCP stdio 子进程 | 每 server 一把 `_lock` | JSON-RPC over stdio 无多路复用，请求-响应需串行 |
| CronScheduler | `threading.Lock` + ThreadPoolExecutor(3) | 保护任务表快照，异步执行不阻塞 tick |

## 配置体系

| 配置 | 位置 | 管理方式 |
|------|------|----------|
| BotConfig | `.env` | 手编，控制 AI 后端、数据路径等基础项 |
| AssistantConfig | `data/assistant_config.json` | 前端 UI，摘要群、提醒群、公众号分组 |
| 定时任务 | `data/cron_jobs.json` | 前端「定时任务」面板或 Agent `create_cron` |
| Skill | `data/skills/{name}/SKILL.md` | Agent `create_skill` / 示例生成 |
| MCP server | `data/user_mcp.json` | 手编或前端 MCPTab |
| iLink 凭据 | `data/ilink_account.json` | 扫码绑定时动态生成 |

## 依赖关系

后端核心依赖：`anthropic`、`openai`、`pydantic`、`pywin32`、`pywebview`、`zstandard`、`pycryptodome`、`Pillow`、`psutil`、`requests`（MCP HTTP 传输）、`chromadb`（向量库）、`fastembed`（本地嵌入）、`onnxruntime`（推理运行时）

前端核心依赖：`react` 19、`framer-motion`、`@phosphor-icons/react`、`qrcode.react`、`tailwindcss` 4

## 模块文档索引

| 模块 | 文档 | 说明 |
|------|------|------|
| Agent 系统 | [modules/agent.md](modules/agent.md) | ReAct 引擎 / 25 工具 / 确认状态机 |
| Skill 体系 | [modules/skill.md](modules/skill.md) | SKILL.md 格式 / 双类型执行 / [SILENT] |
| 通用定时任务 | [modules/cron-scheduler.md](modules/cron-scheduler.md) | cron 语法 / 执行链路 / 与 DigestScheduler 分工 |
| MCP Client | [modules/mcp-client.md](modules/mcp-client.md) | 外部 server 接入 / 心跳降级 / 工具注入 |
| 群聊摘要 | [modules/group-digest.md](modules/group-digest.md) | 定时摘要 + 群档案 + 推送 |
| 关键词提醒 | [modules/keyword-alert.md](modules/keyword-alert.md) | 即时提醒 + 防误触 + 推送 |
| 公众号助手 | [modules/oa-assistant.md](modules/oa-assistant.md) | 摘要 + 即时提醒 |
| AI 后端 | [modules/ai-backend.md](modules/ai-backend.md) | provider 检测 / 摘要 / 流式对话 |
| 微信推送 | [modules/ilink-push.md](modules/ilink-push.md) | iLink Bot 推送通道 |
| 通知队列 | [modules/notification-outbox.md](modules/notification-outbox.md) | 统一通知模型 |
| RAG 语义检索 | — | 本文档已涵盖 RAG 架构设计 |
| MCP Server（反向） | — | 本文档已涵盖 MCP 协议和端口 |
