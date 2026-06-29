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
├── router.py                 # 消息路由（持久化 + 记忆整合触发）
├── main.py                   # CLI 入口
├── desktop.py                # PyWebView 桌面入口
├── admin.py                  # 管理员命令
├── nickname.py               # 昵称服务
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
│   └── outbox.py             # SQLite 通知队列
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
4. WeChat Backend（本地数据后端）
5. 健康监控 daemon（30s 心跳）
6. Assistant 子系统（如果 assistant_enabled）：
   - AlertEngine（关键词提醒）
   - DigestScheduler（群聊 + 公众号定时摘要）
   - OAMonitorEngine（公众号即时提醒）
   - Outbox（通知队列）
7. backend.start(callback) — 阻塞式轮询消息
```

### 关键约束

Web server 是 daemon 线程，主进程退出后服务消失。源码模式必须保持主线程存活：

```powershell
$env:PYTHONPATH='.'
python -c "from src.web.server import start_web_server; import time; t = start_web_server(); [time.sleep(1) for _ in iter(int, 1)]"
```

启动后通过 `POST /api/start` 初始化 bot 后端。

## 消息流

```
微信收到新消息
    │
    ▼
本地数据后端轮询读取
    │  去重 + 排除自身 + 类型标准化
    ▼
MessageRouter.handle(msg)
    │  持久化到 SQLite
    │  可选触发群记忆整合
    ▼
Bot._wrapped_callback(msg)
    │
    └── assistant_alert.check(msg)   ← 关键词即时提醒检测
            │ 命中 → Outbox + 可选 iLink 推送
            ▼
        通知队列 / 微信私聊

定时分支：
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
| 运行状态 | Dashboard | 服务状态、系统健康、即时提醒/定时任务总览 |
| 系统配置 | ConfigPanel | AI 后端、数据路径、消息推送、功能开关、AI 调试台 |
| 群聊助手 | AssistantPanel | 定时摘要、群档案、关键词提醒、通知中心 |
| 会话管理 | ChatTab | 会话列表、消息浏览、图片/语音、导出 |
| 收藏助手 | FavoritesTab | 收藏浏览、AI 对话、导出 |
| 朋友圈助手 | MomentsTab | 朋友圈浏览、图片/视频、HTML 归档 |
| 公众号助手 | OATab | 分组、文章列表、摘要生成、即时提醒 |
| 运行日志 | LogViewer | 实时日志查看 |

## 线程安全

| 组件 | 保护机制 | 说明 |
|------|----------|------|
| 本地数据原生调用 | `threading.Lock` + 超时 | 串行化所有 ctypes 调用，超时返回 `None` |
| 去重集合 | `_lock` | 保护所有 mutation |
| WebSocket 广播 | snapshot-then-send | 广播前快照订阅者列表 |
| .env 写入 | 原子写入 + 文件锁 | tmp + `os.replace()` |
| 进程退出 | `atexit` + 硬杀 | 确保关闭 EXE 不留僵尸进程 |

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