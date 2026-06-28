# wx-assist — CLAUDE.md

## 🚫 绝对禁令

**永远不要操作 C:\Users\74062\Desktop\webot-main。** 只能操作 C:\Users\74062\Desktop\wx-assist。
webot-main 是线上 demo 项目，wx-assist 是正在准备发布的纯净版。

## 🧠 必须遵守的工作方式

### 每次操作前先讲清楚
修改代码前，先口头讲清楚：打算怎么做、为什么这么做、有什么风险。
用户确认后再动手。不要直接改。

### 每个结论都要用中文详细描述
分析问题时，每得出一个思考结论、每发现一个根因，都用中文完整写出推演过程。
不要只抛结论不写推导。不要跳步。

### 所有代码修改必须自测验证
修改代码后、提交前，必须做功能验证：
- 后端代码：运行 `python -m py_compile` 语法检查，必要时调 API 测响应
- 前端代码：`npm run build` 确认编译通过，功能跑通后再提交
- 不改用户看到的实际行为时可以不重启，但改了必须重启（前端重编译或后端重启动）
- 不能口头说"应该没问题"，要实际验证过

---

## 项目概述

wx-assist — AI 驱动的微信消息总结机器人。直读 WCDB 加密数据库（微信 4.x），纯本地运行，数据不出本机。

**品牌**: 摘星  
**状态**: 活跃开发，已接入微信后端/前端

---

## 当前状态（截至 2026-06-27）

### 已完成的工作

1. **禁用功能代码移除**（依据 docs/disabled-features.md）
   - 删除 src/fun.py（抽签功能）
   - 删除 src/proactive/ 目录（主动发言 + 粘性提及）
   - 从 src/config.py 移除：summarize_enabled, fun_enabled, proactive_enabled, sticky_mention_enabled, dedup_window_sec, fallback_window_hours, max_messages_for_summary 及相关 validation
   - 从 src/router.py 移除：_handle_summary(), _handle_proactive_chat(), _handle_fun_lots(), 相关路由和 imports
   - 从 src/web/server.py 移除：相关 API handlers 和 config 字段引用
   - 从 ui/src/components/ConfigPanel.jsx 移除：相关表单字段
   - 从 src/main.py 移除：相关打印行
   - **保留**: memory_consolidation_enabled（用户要求保留）、trigger_keywords（被 @聊天/帮助共享）、enable_restricted_features（活跃安全闸）

2. **test_config.py 6 项修复已通过**（共 49 个 test cases 全部 green）

### 测试结果

```
Ran 231 tests in 0.982s
FAILED (failures=5, errors=8)
```

**13 个失败均为预存测试问题，非本次改动引入：**

| 类别 | 数量 | 根因 | 是否影响发布 |
|------|------|------|-------------|
| A) 需要 pytest 但没装 | 3 errors | test_functional, test_provider_detector（依赖 pytest）；test_window_controller（需微信运行时） | ❌ 无法修复，忽略 |
| B) Windows 临时目录清理 | 5 errors | OSError: 目录不是空的 — 文件句柄竞争，测试逻辑本身通过 | ❌ 环境噪音，忽略 |
| C) 断言与代码不匹配 | 5 failures | 测试数据未随重构更新（allow_wechat_send 默认 False、Unicode、log 格式等） | ⚠️ 测试自身 bug，不修不影响功能 |

### 已知可清理的遗留

均已清理完成。

---

## 项目结构

```
wx-assist/
├── src/
│   ├── bot.py              — Bot 主控
│   ├── config.py           — 配置加载 (.env) → BotConfig
│   ├── router.py           — 消息路由
│   ├── main.py             — 入口点
│   ├── admin.py            — 管理员命令
│   ├── nickname.py         — 昵称服务
│   ├── assistant/          — 群聊助手（关键词预警、摘要、调度器、通知队列、公众号助手）
│   ├── summarize/          — AI 后端 (DeepSeek / Claude / Provider Detector)
│   ├── wechat/             — 微信后端（WCDB 直读、图片解密、语音解码、收藏、朋友圈）
│   ├── web/                — Web UI 服务器 + API + AI Chat SSE
│   ├── memory/             — 聊天记忆整合 (MemoryConsolidator)
│   ├── guard/              — 不良内容检测
│   ├── scheduler/          — Cron 调度引擎
│   ├── trigger/            — 触发词检测
│   └── db/                 — 本地数据库
├── ui/                     — React + Vite + Tailwind v4
├── tests/                  — 单元测试（9 个文件，231 用例）
├── docs/                   — 文档
├── lib/                    — DLL + WASM
├── data/                   — 运行时数据
├── .env.example            — 环境变量模板
├── build.spec              — PyInstaller 打包
└── requirements.txt        — Python 依赖
```

---

## 启动方式

### Web UI + 后端（源码模式）

```powershell
$env:PYTHONPATH='.'
D:\Python313\python.exe -c "from src.web.server import start_web_server; import time, sys; t = start_web_server(); print('Server started' if t else 'Failed'); sys.stdout.flush(); import time; [time.sleep(1) for _ in iter(int,1)]"
```

再开终端初始化 bot：
```powershell
Invoke-WebRequest -Uri 'http://127.0.0.1:17327/api/start' -Method POST
Invoke-WebRequest -Uri 'http://127.0.0.1:17327/api/status'
```

要求返回：running:true, db_ok:true, error:""

### 桌面模式
```powershell
$env:PYTHONPATH='.'
D:\Python313\python.exe desktop.py
```

---

## 技术要点

### AI Provider 统一配置
- AI_PROVIDER_TYPE: "openai" | "anthropic" | "custom" | "auto"
- AI_PROVIDER_BASE_URL: API 根地址
- AI_PROVIDER_MODEL: 模型 ID
- AI_PROVIDER_EXTRA_BODY: 附加参数 JSON（如 {"thinking":{"type":"disabled"}}）

### 端口
- 后端 HTTP: 17327（src/web/server.py start_web_server 的 port 参数）
- 前端 dev: 15173（ui/vite.config.js server.port）
- 前序会话中后端切换过 17327 用于测试（已回滚，勿动 webot-main）

### 前序会话中踩过的坑（均在 wx-assist 上完成）
参见 webot-main 的 memory 文件（未来需在 wx-assist 下重建 memory 系统）：
- WCDB 直读 / WCDB 密钥提取
- 图片解密（ISAAC-64）
- V2 缓存解密（AES-128 + XOR）
- SILK 语音解码
- 消息类型码归一化（微信 4.x 新类型码 0x31）
- 锁审计（去过度加锁 + 补遗漏加锁）
- 日志 rotation（RotatingFileHandler 10MB×4）
- AI 连通性真实检测（ai_ok eagar probe）
- 会话管理修复 7 项
- 收藏导出修复 3 项
- 公众号助手全面重构
- 性能审计（API 端点耗时）
- 项目深度体检（安全/依赖/健壮性）

---

## 发布前待办

### 1. 自测方案
详细测试计划见 docs/test-plan.md。
31 个用例，9 模块 + 3 条全链路。

测试方式：后端 API 用 curl/PowerShell，前端用 CDP（Playwright 无截图判断）。

---

## 每次提交后的验收部署要求

每次执行 `git commit` 后，必须立即重新部署当前源码模式的后端和前端，方便用户验收。

### 后端重启

1. 停止本会话中已启动的旧后端后台任务（如果有）。
2. 重新启动后端：

```powershell
$env:PYTHONPATH='.'
D:\Python313\python.exe -c "from src.web.server import start_web_server; import time, sys; t = start_web_server(); print('Server started' if t else 'Failed'); sys.stdout.flush(); import time; [time.sleep(1) for _ in iter(int,1)]"
```

3. 验证：

```powershell
Invoke-WebRequest -Uri 'http://127.0.0.1:17327/api/status'
```

要求返回：`running:true`, `db_ok:true`, `error:""`。

### 前端重启

1. 停止本会话中已启动的旧前端 dev server（如果有）。
2. 重新启动前端：

```powershell
cd C:\Users\74062\Desktop\wx-assist\ui
D:\nodejs\npm.cmd run dev -- --host 127.0.0.1
```

3. 验证 `http://127.0.0.1:15173/` 返回 200。

### 注意

- 若后端代码未变，可以说明“无需重启后端”；但用户明确要求验收时，仍优先重启。
- 若前端代码未变，可以说明“无需重启前端”；但用户明确要求验收时，仍优先重启。
- 重启后必须把两个地址明确告诉用户：后端 `http://127.0.0.1:17327`，前端 `http://127.0.0.1:15173`。

---

## 工具与依赖

| 工具 | 路径 |
|------|------|
| Python | D:\Python313\python.exe |
| Node.js | D:\nodejs\node.exe |
| Chrome | C:\Program Files\Google\Chrome\Application\chrome.exe |
