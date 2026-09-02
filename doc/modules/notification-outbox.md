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
| `cron` | CronScheduler | 通用 skill 定时任务执行结果 |

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
| push_status | TEXT | 统一推送结果："success" / "partial" / "failed" / "skipped" / "pending_push"，与 task_center.push_status 同一套词表 |
| push_channel | TEXT | 推送通道；多渠道结果无法归属单一通道时为空，逐渠道明细见 im_delivery_attempts |
| push_error | TEXT | 推送错误信息（partial 时保留失败渠道的原因） |
| created_at | TEXT | ISO-8601 创建时间 |

### 推送状态三态判定

一次推送会扇出到所有已绑定的 IM 渠道（iLink / QQ bot / 飞书），聚合规则由
`src/im/delivery.py::aggregate_status` 统一给出，原始推送与任务中心重推共用：

| 各渠道结果 | push_status | 任务中心展示 | 可重推 |
|---|---|---|---|
| 全部送达 | `success` | ✓ 已推送 | 否 |
| 部分送达 | `partial` | △ 部分成功（琥珀色） | **否** |
| 全部失败 | `failed` | ✗ 推送失败 | 是 |
| 无绑定渠道 | `skipped` | 推送中/跳过 | 否 |

`partial` 是**终态**：已有渠道收到消息，重推会让这些渠道收到重复内容，因此
既不显示重推按钮，也不进"一键重推"候选集（`get_failed_push_tasks`）。
它同样不计入任务中心的失败红点与"失败"筛选，但在 Outbox 去重
（`query_by_url(only_success=True)`）和推送成功率统计中**算作已送达**。

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
- `cron`：skill 执行结果文本（或 `[CRON 错误] ...` 错误信息）

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

### 推送记录（消息推送 → 推送记录）

数据源是 `im_delivery_attempts`（逐渠道投递审计），不是 Outbox 本身 —— 一次推送扇出到 N 个渠道就有 N 行。Outbox 只负责提供「原始内容」。

**关键约束：每个业务推送调用点构造 `DeliveryRequest` 时必须传 `outbox_id`。**
`api_handlers._to_record()` 靠它回查 Outbox 拿到 `title` / `content` / 可读群名。漏传的后果是该类型的记录永远显示原始 chatroom ID 加「原始推送内容不可用」——`keyword_alert` 曾因此坏了很久（`b19678a` 只补了读取侧，写入侧 `alert.py` 一直没传）。

| 机制 | 说明 |
|------|------|
| 读侧回退 | `outbox_id` 为 0 且 `source_id` 是纯数字时，用 `source_id` 当 Outbox 主键回查。用于恢复漏传 `outbox_id` 时期写入的历史记录，无需重推。非数字（如飞书 `om_xxx`）不回退，避免把平台消息 ID 当主键 |
| 排除非业务类型 | `agent_reply`（Agent 双向对话回复，不是通知，正文也从未落盘）、`binding_test`、`test`、`test_multi` 在 SQL 层排除，见 `api_handlers._HIDDEN_PUSH_SOURCE_TYPES` |
| 排除位置 | 必须下推到 SQL、在 `LIMIT` 之前生效。先取回窗口再过滤会让每页条数不足、分页与计数失真；类型/状态筛选同理（曾导致选「定时任务」明明有 12 条却显示空） |
| 渠道健康探测例外 | `list_recent_failures()` **不**排除 `agent_reply` —— 它的失败正是渠道不可用的有效信号，排除会漏报 |

`agent_reply` 没有 Outbox 行，`im_delivery_attempts` 也没有存正文的列，`agent_memory` 只存 LLM 摘要，因此其回复正文在任何地方都没有落盘。当前决定是不展示这类记录，而非补存正文。

### 外部 Agent 集成

第三方脚本轮询 `GET /api/assistant/notifications/pending` 取出所有待投递通知，自行投递（邮件/钉钉/短信等），然后调用 `POST /api/assistant/notifications/{id}/ack` 标记已投递。

## 代码位置

| 组件 | 文件 |
|------|------|
| Outbox 类 | `src/assistant/outbox.py` |
| 通知 API | `src/web/server.py` |
| 投递审计（im_delivery_attempts） | `src/im/delivery.py` |
| 推送记录 API（含类型排除与 Outbox 回填） | `src/web/api_handlers.py` `handle_push_history` / `_to_record` |
| 前端 AssistantPanel（通知中心） | `ui/src/components/AssistantPanel.jsx` |
| 前端 ConfigPanel（推送记录） | `ui/src/components/ConfigPanel.jsx` `PushHistory` |
| 前端 Dashboard（即时提醒/定时任务卡片） | `ui/src/components/Dashboard.jsx` |