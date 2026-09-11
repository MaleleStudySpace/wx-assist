# 关键词提醒

## 1. 功能定位

关键词提醒是纯规则功能，不需要 AI。系统收到新消息后，按群组和关键词匹配，命中后写入通知队列，并根据绑定渠道配置进行投递。

## 2. 匹配流程

```text
新消息
  → 清理内部标识和无效内容
  → 检查助手总开关
  → 检查消息时间窗口
  → 匹配群组
  → 不区分大小写的关键词子串匹配
  → Outbox 写入 keyword_alert
  → DeliveryService 投递
```

群组匹配优先使用会话 ID；没有会话 ID 时使用群名大小写不敏感精确匹配。关键词之间是 OR 关系。

## 3. 防误触

- 超过 5 分钟的历史消息不触发；
- 同一群同一关键词 5 秒内冷却；
- 已触发消息会持久化去重；
- 助手关闭或群组关闭时直接跳过。

## 4. 通知处理

通知可以在 AssistantPanel 中查看、确认和忽略，也可以由外部程序通过 pending API 拉取后自行投递。

实际通知渠道由消息推送页面中已经绑定的平台决定，旧版单一目标字段只作兼容。

## 5. 代码位置

- 规则引擎：`src/assistant/alert.py`
- 配置：`src/assistant/config.py`
- 通知：`src/assistant/outbox.py`
- 投递：`src/im/delivery.py`
- 前端：`ui/src/components/AssistantPanel.jsx`
