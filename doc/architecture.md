# 项目架构

## 1. 产品概览

摘星是一款本地运行的微信助手。它由桌面外壳、Python 服务、React 界面、本地数据层、AI 能力层和通知渠道组成。

```text
桌面应用 / 浏览器
        │
        ▼
本地 Web 服务（HTTP + WebSocket + SSE）
        │
        ├── 配置与状态
        ├── 消息/收藏/朋友圈 API
        ├── 助手与任务 API
        └── AI 对话 API
                │
                ▼
        Bot 主控与业务模块
        ├── 本地消息读取与标准化
        ├── MessageStore 本地保存
        ├── Assistant（提醒、摘要、公众号）
        ├── Agent / Skill / MCP
        ├── RAG 本地语义检索
        └── IM Delivery 消息投递
```

## 2. 技术组成

| 层 | 组成 |
|---|---|
| 桌面入口 | PyWebView / WebView2，也可用浏览器访问 |
| 后端 | Python 3.13，标准库 HTTP 服务 |
| 前端 | React + Vite + Tailwind |
| 本地数据 | SQLite、JSON、文件缓存 |
| AI | Anthropic、OpenAI-compatible Provider、Stub 降级后端 |
| 实时通信 | WebSocket 状态/事件、SSE 流式输出 |
| 任务 | 摘要调度器、通用 Cron 调度器、Skill 引擎 |
| 扩展 | MCP Client 与本地 MCP Server |
| 通知 | 微信 iLink、QQ Bot、飞书等绑定渠道 |

## 3. 启动流程

```text
desktop.py
  → 启动本地 Web 服务
  → 首次运行进入配置向导
  → 用户点击启动
  → load_config()
  → 初始化本地数据库和消息路由
  → 创建助手、任务、Agent、Skill、MCP、RAG 组件
  → 启动消息读取和通知服务
```

Web 服务和 Bot 是两个生命周期。Web 页面可以先启动，Bot 尚未启动时状态显示为“已停止”属于正常状态。

## 4. 消息处理流程

```text
本地数据读取
  → 消息标准化、去重、内容清洗
  → 写入本地消息库
  → MessageRouter 分发
      ├── 关键词提醒
      ├── 可选记忆整理
      ├── 可选语义索引
      └── 私聊 Agent
```

普通消息保存不依赖 AI。摘要、记忆、Agent 和语义检索分别由独立模块处理，某一项失败不应阻断消息保存。

## 5. AI 不可用时的边界

应用允许在未配置 AI 时启动，并使用 Stub 后端维持非 AI 功能：

- 消息、收藏、朋友圈的浏览和导出仍可用；
- 关键词提醒、文章读取、脚本任务仍可用；
- 摘要、AI 对话、Agent 和 prompt Skill 会提示需要配置 AI；
- 本地语义检索不调用云端 AI，但依赖本地向量模型和索引服务。

详见 [AI 后端与降级](modules/ai-backend.md)。

## 6. 配置分层

| 配置 | 作用 | 生效方式 |
|---|---|---|
| `.env` | 基础运行参数、AI Provider、数据目录 | 多数项目需要重启 Bot |
| Assistant 配置 | 提醒、摘要、公众号分组 | 保存后热更新 |
| Cron 配置 | 通用 Skill 定时任务 | 重新加载后生效 |
| MCP 配置 | 外部扩展服务 | 可在界面中管理 |
| 平台绑定 | 通知渠道凭据和目标 | 按渠道绑定/解绑 |

## 7. 前端页面

| 页面 | 功能 |
|---|---|
| Dashboard | 运行状态、即时提醒、定时任务概览 |
| 系统配置 | AI、数据目录、通知渠道和功能开关 |
| 群聊助手 | 关键词提醒、群聊摘要和通知中心 |
| 定时任务 | Cron、Skill 和执行历史 |
| 会话管理 | 会话、消息、媒体、导出、AI 对话 |
| 收藏助手 | 收藏浏览、筛选、导出和 AI 对话 |
| 朋友圈 | 动态浏览、媒体、导出和 AI 总结 |
| 公众号助手 | 账号、分组、文章、摘要和即时提醒 |
| MCP 管理 | 外部扩展服务和工具开关 |
| 日志/任务中心 | 运行日志和全局任务状态 |

## 8. 代码入口

- Bot 生命周期：`src/bot.py`
- 配置：`src/config.py`、`src/assistant/config.py`
- Web 服务：`src/web/server.py`、`src/web/api_handlers.py`
- AI：`src/summarize/`、`src/web/ai_chat.py`
- Agent：`src/agent/`
- 助手：`src/assistant/`
- 调度：`src/scheduler/`、`src/utils/cron.py`
- 通知：`src/im/`
- 前端：`ui/src/`
