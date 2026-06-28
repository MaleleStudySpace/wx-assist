# 摘星 · wx-assist

> AI 驱动的个人消息助理 — 群聊摘要、关键词提醒、公众号追踪、收藏管理、朋友圈归档。
> **100% 本地运行 · 零数据上传 · 开源可审**

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-blue?style=flat-square&logo=windows" alt="Platform" />
  <img src="https://img.shields.io/badge/python-3.13-green?style=flat-square&logo=python" alt="Python" />
  <img src="https://img.shields.io/badge/AI-Claude%20%7C%20DeepSeek-purple?style=flat-square" alt="AI Backend" />
  <img src="https://img.shields.io/badge/ui-React%20%2B%20Tailwind-cyan?style=flat-square&logo=react" alt="UI" />
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=flat-square" alt="License" />
</p>

---

## ✨ 功能一览

### 🧠 AI 群聊摘要

对群里说一句「总结一下」，立刻获得按话题分类、附关键人物和时间线的摘要。支持定时调度、仅摘要未读、大消息量自动分片。

### 💬 AI 对话

在收藏、朋友圈、群聊中直接与 AI 对话。SSE 流式响应 + Token 压缩，上下文可达 200K 字符。

### 📰 公众号助手

- 自动追踪关注的公众号文章
- 按分组管理（科技、财经、生活…）
- 每组可配置独立 AI 摘要提示词
- 定时生成摘要
- 智能回溯：自动向前搜索直到找到已读文章

### ⭐ 收藏助手

- 浏览微信收藏内容（文本/图片/视频/语音/链接/笔记）
- 高清媒体资源导出
- 标签分类筛选
- AI 对话：基于收藏内容提问

### 👁 朋友圈助手

- 浏览朋友圈时间线（文字/图片/视频/链接）
- 高清媒体资源导出
- 内容归档快照
- 批量导出为 JSON + HTML + 本地化图片

### 💬 会话管理

- 按联系人/群聊分组浏览聊天记录
- 全文搜索（消息内容/发送者）
- 高清图片查看
- 语音播放
- 导出聊天记录为 HTML

### 🤖 群聊助手

- 定时摘要：支持每天/工作日/自定义星期 + Cron 高阶配置
- 关键词提醒：自定义触发词即时通知
- 群成员搜索 + 共同群聊 + 好友标记

---

## 🚀 快速开始

### 前置要求

- Windows 10/11
- Python 3.13+
- 微信 4.x（Windows 桌面版）
- Node.js 22+

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，填写 AI API Key 等配置
```

### 3. 启动

**桌面模式（推荐）**：

```bash
python desktop.py
```

**仅 Web UI**：

```powershell
$env:PYTHONPATH='.'
python -c "from src.web.server import start_web_server; import time
t = start_web_server()
while True: time.sleep(1)"
# 另一个终端：
Invoke-WebRequest -Uri 'http://127.0.0.1:17327/api/start' -Method POST
```

浏览器打开 `http://127.0.0.1:17327`，按引导完成首次设置。

### 4. 打包 EXE

```bash
cd ui && npm install && npm run build && cd ..
pyinstaller build.spec
# 输出: dist/wx-assist.exe
```

---

## ⚙️ 配置

主要配置项通过 `.env` 文件管理：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AI_BACKEND` | AI 后端：`deepseek` 或 `claude` | `deepseek` |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | — |
| `POLL_INTERVAL_SEC` | 消息轮询间隔（秒） | `1.0` |
| `DEDUP_WINDOW_SEC` | 同群去重窗口（秒） | `60` |

完整配置项见 [.env.example](.env.example)。

---

## 🔒 安全设计

- **数据纯本地**：所有消息数据仅在本机处理，不上传任何服务器
- **API Key 本地存储**：AI API Key 仅保存在本地 `.env` 文件中
- **PII 脱敏**：联网搜索前自动剥离手机号/身份证号/邮箱
- **原子写入**：配置文件使用 `os.replace` 防止崩溃时损坏

---

## 📁 项目结构

```
src/
├── bot.py                  # Bot 主控
├── config.py               # 配置加载
├── router.py               # 消息路由
├── summarize/              # AI 后端
├── wechat/                 # 数据后端
├── assistant/              # 群聊助手
│   ├── config.py           # 助手配置管理
│   ├── digest.py           # 摘要生成
│   ├── alert.py            # 关键词提醒
│   ├── outbox.py           # 通知队列
│   └── scheduler.py        # 调度器
├── web/                    # Web UI 服务器
│   ├── server.py           # HTTP + WebSocket
│   ├── api_handlers.py     # API 处理
│   └── ai_chat.py          # AI 对话
├── memory/                 # 聊天记忆
├── proactive/              # 主动发言
├── scheduler/              # 调度引擎
├── guard/                  # 内容检测
├── trigger/                # 触发词检测
├── db/                     # 本地数据库
└── utils/                  # 工具函数
ui/                         # React 前端
lib/                        # 依赖模块
desktop.py                  # 桌面入口
desktop_mac.py              # macOS 桌面入口
build.spec                  # 打包配置
```

---

## 🧑‍💻 开发

```bash
# 前端开发
cd ui && npm install && npm run dev

# 后端开发
$env:PYTHONPATH='.'
python -c "from src.web.server import start_web_server; import time
t = start_web_server()
while True: time.sleep(1)"
```

---

## 📄 License

[MIT](LICENSE)
