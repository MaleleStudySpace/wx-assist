# 通知队列

## 1. 功能定位

通知队列统一保存关键词提醒、摘要、文章提醒和通用任务结果。它将“业务已经生成内容”和“内容是否成功投递”分开记录，方便前端查看、重试和排障。

## 2. 通知类型

- `keyword_alert`：关键词命中；
- `group_digest`：群聊摘要；
- `oa_digest`：公众号摘要；
- `oa_article_alert`：公众号文章提醒；
- `cron`：通用 Skill 任务结果。

## 3. 状态

| 状态 | 含义 |
|---|---|
| `pending` | 等待处理 |
| `delivered` | 已确认处理 |
| `ignored` | 用户忽略 |
| `failed` | 业务或投递失败 |

推送结果还会记录 `success`、`partial`、`failed`、`skipped` 等状态。多渠道投递时，逐渠道明细由投递审计记录保存。

## 4. 业务流程

```text
业务模块生成内容
  → Outbox.add()
  → DeliveryService 扇出到绑定渠道
  → 回写 push_status / push_error / push_channel
  → TaskCenter 同步任务推送状态
```

没有绑定渠道时，业务内容仍可保存在队列中，推送结果为 skipped，不应误报为摘要生成失败。

## 5. 前端消费

- AssistantPanel：通知中心、类型过滤、状态过滤、确认和忽略；
- Dashboard：即时提醒和定时任务概览；
- ConfigPanel：推送记录和逐渠道结果；
- TaskCenter：跨功能任务生命周期。

## 6. 代码位置

- 队列：`src/assistant/outbox.py`
- 投递：`src/im/delivery.py`
- 通知 API：`src/web/server.py`、`src/web/api_handlers.py`
- 前端：`ui/src/components/AssistantPanel.jsx`、`ConfigPanel.jsx`、`TaskCenter.jsx`
