# wx-assist · 摘星

> AI 驱动的本地微信消息助手。纯本地运行，数据不出本机，定时生成群聊摘要、公众号摘要、关键词即时提醒，并可通过 iLink 推送到微信。

## 项目定位

wx-assist 是一个**只读 + 通知**型助手：从微信本地数据库读取消息与文章，结合 AI 生成结构化摘要和即时提醒，再通过独立的 iLink 推送通道送达微信私聊。它不主动操控微信窗口发言、不接入 Web API，所有数据读取与处理都在本机完成。

## 核心能力

| 能力 | 说明 |
|------|------|
| 群聊定时摘要 | 按 cron 调度，定时拉取群消息生成结构化摘要，支持群档案/风格预设/自定义指令 |
| 群聊关键词提醒 | 检测到配置关键词即时生成提醒通知，可推送到微信 |
| 公众号摘要 | 按分组定时汇总公众号新文章，支持多种摘要模板 |
| 公众号即时提醒 | 监控关注公众号，新文章发布即刻推送通知 |
| 会话管理 | 浏览聊天记录、富媒体消息渲染、聊天导出归档 |
| 收藏助手 | 收藏浏览、按类型/标签筛选、AI 对话查询、导出 |
| 朋友圈助手 | 朋友圈浏览、图片灯箱、视频下载、HTML 归档 |
| AI 对话 | 基于聊天记录/收藏的 SSE 流式 AI 对话，支持上下文压缩 |
| 微信推送 | iLink Bot 通道，跨设备推送摘要/提醒到微信私聊 |

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.13，纯 `http.server.ThreadingHTTPServer`，零外部 Web 框架 |
| 前端 | React 19 + Vite 8 + Tailwind 4 |
| 桌面容器 | PyWebView（WebView2） |
| AI 后端 | DeepSeek / Claude / 任意 OpenAI 兼容 API，统一 provider 自动检测 |
| 实时通信 | WebSocket（状态/事件推送）+ SSE（AI 流式输出） |
| 调度 | 自定义 daemon 线程（群聊/公众号摘要） |
| 持久化 | SQLite（消息/通知队列）+ JSON（助手配置） |
| 打包 | PyInstaller → 单文件 `wx-assist.exe` |

## 快速开始

### 1. 环境准备

- Windows 10/11，微信电脑版已安装并登录
- Python 3.13+，Node.js 18+
- 安装依赖：`pip install -r requirements.txt`，`cd ui && npm install`

### 2. 前端构建

```powershell
cd ui
npm run build
```

### 3. 源码模式运行

```powershell
$env:PYTHONPATH='.'
python -c "from src.web.server import start_web_server; import time, sys; t = start_web_server(); print('Server started' if t else 'Failed'); sys.stdout.flush(); [time.sleep(1) for _ in iter(int,1)]"
```

打开 `http://127.0.0.1:17327` 进入引导流程，获取连接凭证后即可使用。

### 4. 桌面模式 / 打包

```powershell
# 桌面模式
python desktop.py

# 打包 EXE
pyinstaller build.spec
```

## 文档导航

| 文档 | 说明 |
|------|------|
| [architecture.md](architecture.md) | 整体架构、启动流程、消息流、线程模型 |
| [modules/group-digest.md](modules/group-digest.md) | 群聊定时摘要的调度、prompt 架构、推送链路 |
| [modules/keyword-alert.md](modules/keyword-alert.md) | 关键词即时提醒的检测、防误触、推送 |
| [modules/oa-assistant.md](modules/oa-assistant.md) | 公众号摘要 + 即时提醒 |
| [modules/ai-backend.md](modules/ai-backend.md) | AI 后端统一层、provider 检测、流式对话 |
| [modules/ilink-push.md](modules/ilink-push.md) | iLink 微信推送通道 |
| [modules/scheduler.md](modules/scheduler.md) | 调度器设计与 cron 约定 |
| [modules/notification-outbox.md](modules/notification-outbox.md) | 通知队列与统一推送模型 |

## 配置

主配置分两部分：

- **BotConfig**（`src/config.py`）：从 `.env` 加载，控制 AI 后端、数据路径等基础项。模板见 `.env.example`。
- **AssistantConfig**（`src/assistant/config.py`）：持久化到 `data/assistant_config.json`，控制摘要群、提醒群、公众号分组等助手功能，全部通过前端 UI 管理，无需手编。

AI 后端统一配置：

```
AI_PROVIDER_BASE_URL=https://api.example.com
AI_PROVIDER_API_KEY=sk-xxx
AI_PROVIDER_TYPE=auto          # auto | openai | anthropic | custom
AI_PROVIDER_MODEL=DeepSeek-V4-Flash
```

## 项目结构

```
wx-assist/
├── src/
│   ├── bot.py              # 组件编排与生命周期
│   ├── config.py           # BotConfig，.env 加载
│   ├── router.py           # 消息路由与持久化
│   ├── main.py             # CLI 入口
│   ├── desktop.py          # PyWebView 桌面入口
│   ├── assistant/          # 群聊助手（摘要、提醒、公众号、调度、通知队列）
│   ├── summarize/          # AI 后端（provider 检测、摘要、对话）
│   ├── wechat/             # 微信集成（本地数据读取、推送通道、媒体处理）
│   ├── web/                # HTTP + WebSocket 服务 + API + AI Chat SSE
│   ├── memory/             # 群记忆整合
│   ├── guard/              # 不良内容检测
│   ├── scheduler/          # 通用定时任务
│   └── db/                 # 本地 SQLite 持久化
├── ui/                     # React 前端
├── lib/                    # 运行时依赖的原生模块
├── .env.example            # 环境变量模板
├── build.spec              # PyInstaller 打包配置
└── requirements.txt        # Python 依赖
```

## 许可证

本项目仅供个人本地使用学习。使用前请确保遵守当地法律法规与微信用户协议。