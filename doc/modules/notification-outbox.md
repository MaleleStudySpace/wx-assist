# 通知队列（Outbox）

## 一句话说明

统一的 SQLite 持久化通知队列，所有助手功能的触发结果都写入 Outbox，支持推送审计和泛化消费。

## 通知类型

| 类型 | 来源 | 说明 |
|------|------|------|
| `keyword_alert` | AlertEngine | 关键词命中即时提醒 |
| `group_digest` | DigestScheduler | 群聊定时摘要 |
| `oa_digest` | DigestScheduler / 手动 | 公众号摘要 |
| `oa_article_alert` | OAMonitorEngine | 公众号新文章即时提醒 |

## 数据结构

通知存储在 SQLite 数据库 `data/assistant_outbox.db`：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 ID |
| type | TEXT | 通知类型 |
| chat_id | TEXT | 相关会话 ID |
| group_name | TEXT | 群显示名 |
| title | TEXT | 通知标题 |
| content | TEXT | JSON 结构化内容 |
| priority | TEXT | "high" / "normal" |
| status | TEXT | "pending" / "delivered" / "ignored" / "failed" |
| push_status | TEXT | iLink 推送结果 |
| push_channel | TEXT | 推送通道 |
| push_error | TEXT | 推送错误信息 |
| created_at | TEXT | ISO-8601 创建时间 |

### content 字段结构

所有通知的 content 存储 JSON，通用结构：

```json
{
  "group": "群/公众号名称",
  "display": "**群聊:** xxx\\n**消息:** N 条\\n\\n{摘要内容}"
}
```

各类型附加字段：

- `keyword_alert`：`sender`、`keywords`、`message`
- `group_digest`：`lookback_hours`、`mode`、`msg_count`、`digest`
- `oa_digest`：`articles_count`、`digest`
- `oa_article_alert`：`time`、`article_title`、`digest`、`url`

`display` 字段专为 iLink 推送设计，已预格式化为 `format_for_wechat` 可消费的文本。

## 通知生命周期

```
pending（待投递）
    │
    ├── ack() → delivered（已投递）
    ├── ignore() → ignored（已忽略）
    └── 非 pending 状态超 retention_hours → cleanup_expired() 自动删除
```

## 消费方式

| 方式 | 说明 |
|------|------|
| 前端拉取 | `GET /api/assistant/notifications` 带类型/状态过滤 |
| 外部 Agent | `GET /api/assistant/notifications/pending` 取待投递 |
| iLink 即时推送 | 各引擎直接调用 ilink_push 并记录推送结果到 outbox |

### 前端集成

Dashboard 首页的两个卡片分别展示：
- **即时提醒卡片**：`keyword_alert` + `oa_article_alert` 的实时数据
- **定时任务卡片**：`group_digest` + `oa_digest` 的调度状态

AssistantPanel 的通知中心展示所有通知记录，支持类型/状态过滤和 ack/ignore 操作。

### 外部 Agent 集成

第三方脚本轮询 `GET /api/assistant/notifications/pending` 取出所有待投递通知，自行投递（邮件/钉钉/短信等），然后调用 `POST /api/assistant/notifications/{id}/ack` 标记已投递。

## 代码位置

| 组件 | 文件 |
|------|------|
| Outbox 类 | `src/assistant/outbox.py` |
| 通知 API | `src/web/server.py` |
| 前端 AssistantPanel（通知中心） | `ui/src/components/AssistantPanel.jsx` |
| 前端 Dashboard（即时提醒/定时任务卡片） | `ui/src/components/Dashboard.jsx` |