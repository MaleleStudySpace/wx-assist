# 微信推送通道（iLink Push）

## 一句话说明

通过 iLink Bot API 将摘要/通知推送到用户微信私聊，跨设备、无需微信 PC 在线。

## 数据流

```
用户在 ConfigPanel 点击"绑定微信 Bot"
    │
    ▼
GET /api/ilink/qrcode → 获取二维码 URL 和 ID
    │
    ▼
前端 QRCodeSVG 渲染二维码
    │
    ▼ 用户用手机微信扫码
轮询 GET /api/ilink/qrcode-status?qrcode=...
    │  wait → scanned → confirmed
    ▼
绑定成功 → 写入 data/ilink_account.json
    │  弹窗提示"请立即在微信中给 Bot 发一条消息"
    ▼
用户在微信中给 Bot 发消息（激活通道）
    │
    ▼ 后续推送
scheduler / oa_digest / alert / oa_monitor
    │  检查 push_target == "ilink"
    │  format_for_wechat(text) → 截断 4000 字
    │  ilink.send_message(text)
    ▼
消息出现在用户微信私聊中
```

## iLink 与键盘操控模式对比

| 维度 | iLink Push | 键盘操控模式 |
|------|------------|-------------|
| 传输方式 | HTTP API | Win32 键盘模拟 |
| 需要微信 PC 在线？ | 否 | 是 |
| 目标 | Bot → 用户私聊 | 任意群聊/联系人 |
| 跨设备 | 是 | 否 |
| 速率限制 | 2.5s 间隔 | — |
| 消息长度 | 4000 字截断 | — |

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/ilink/status` | GET | 绑定状态 |
| `/api/ilink/qrcode` | GET | 获取二维码 |
| `/api/ilink/qrcode-status` | GET | 轮询扫码状态 |
| `/api/ilink/bind` | POST | 绑定账号 |
| `/api/ilink/unbind` | POST | 解绑（两步确认） |
| `/api/ilink/test-push` | POST | 发送测试消息 |

## send_message 流程

```
1. 验证已绑定 + 文本非空
2. 截断到 4000 字
3. 速率限制：等待 MIN_SEND_INTERVAL_SEC=2.5s
4. 构建请求体 → POST iLink API
5. ret=-2（速率限制）→ 指数退避重试（3/6/12 秒）
6. errcode=-14（会话过期）→ 返回错误，不重试
```

## 凭据管理

- 文件：`data/ilink_account.json`
- 字段：`bot_token`、`account_id`、`base_url`、`user_id`、`created_at`
- 原子写入：tmp + `os.replace()`
- **不存 .env**：凭据是动态的（扫码绑定时生成）

## 前端 UI 状态机

```
未绑定 → 点击"绑定微信 Bot" → binding
binding → QR 码显示 + 轮询 → confirmed
confirmed → 弹窗提示激活 → 已绑定
已绑定 → 显示 bot/user ID + "发送测试消息" + "解除绑定"
```

## 解除绑定（两步确认）

第一次点击显示"确认解除绑定？"，5 秒内再次点击才真正解绑，防止误操作。

## 前端消费

Dashboard 首页"即时提醒"卡片和"定时任务"卡片中，推送到微信的条目会显示 `推送` 标签。iLink 推送结果通过 WebSocket 事件广播到前端，自动弹 Toast。

## 关键设计决策

### 1. QR 码绑定

iLink Bot API 要求通过微信 QR 码登录流程获取 `bot_token`。Bot 请求 QR 码，用户用手机微信扫描，API 返回 `bot_token`。这是微信 Bot 标准授权模型，无用户名密码流程。

### 2. 推送与键盘操控分离

iLink 用于推送通知到私聊，不操控微信窗口。键盘操控模式（已弃用）用于在群内发言。

### 3. 推送结果实时广播

每次推送后通过 WebSocket 广播结果，前端实时显示成功/失败提示。失败时区分普通错误和会话过期，后者提示用户重新绑定。

## 代码位置

| 组件 | 文件 |
|------|------|
| ILinkPush 类 | `src/wechat/ilink_push.py` |
| iLink 配置（共享） | `src/wechat/ilink_push.py`、`src/wechat/ilink_account.json` |
| 群摘要推送调用 | `src/assistant/scheduler.py` |
| 关键词提醒推送调用 | `src/assistant/alert.py` |
| OA 摘要推送调用 | `src/assistant/scheduler.py`（_generate_oa_digest） |
| OA 即时提醒推送调用 | `src/assistant/oa_monitor.py` |
| 前端绑定 UI | `ui/src/components/ConfigPanel.jsx`（PushSection） |