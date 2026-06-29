# 关键词即时提醒（Keyword Alert）

## 一句话说明

监听群消息，检测到配置的关键词时生成即时通知，写入 Outbox 并可选通过 iLink 推送到微信私聊。

## 数据流

```
微信收到新消息 → 本地数据后端轮询
    │
    ▼
Bot._wrapped_callback(msg)
    │
    └── AlertEngine.check(msg)
            │
            ▼
        前置检查：assistant_enabled?
            │ NO → return None
            │ YES ↓
        遍历 alert_groups
            │ 对每个 AlertGroup:
            │   enabled? → 跳过
            │   群匹配 → chat_id 精确匹配 或 group_name 匹配
            │   keywords 非空?
            ▼
        关键词匹配（子串包含，不区分大小写）
            │ 无命中 → 跳过
            │ 命中 ↓
        构建通知 → outbox.add(type="keyword_alert")
            │
            ├── push_target == "ilink"? → iLink 推送
            └── 仅入队，待前端/Agent 拉取
```

## 命中规则

| 规则 | 说明 |
|------|------|
| 匹配方式 | 子串包含（`keyword.lower() in content.lower()`） |
| 大小写 | 不区分 |
| 多关键词 | OR 逻辑，任意一个命中即触发 |
| 群匹配 | `chat_id` 精确匹配优先；无 chat_id 时用 `group_name` 精确匹配 |
| 命中返回 | 仅触发第一个匹配的 AlertGroup，后续群不再检查 |

## 防误触

| 机制 | 值 | 说明 |
|------|-----|------|
| 消息年龄检查 | 300s | 超 5 分钟的旧消息不触发，防启动时对历史消息误报 |
| 关键词冷却 | 5s | 同一群同一关键词短时间内不重复触发 |
| 消息内容清洗 | — | 消息文本中的 `wxid_xxx` / `gh_xxx` 在匹配前剥离 |

## 消费路径

### 路径 A：Web Dashboard

```
前端 AssistantPanel 加载通知列表
    │
    ▼
GET /api/assistant/notifications?type=keyword_alert&status=pending
    │
    ▼
Outbox.list_notifications() → 从 SQLite 查询
    │
    ▼
用户点击"已读" → POST .../ack → status="delivered"
用户点击"忽略" → POST .../ignore → status="ignored"
```

### 路径 B：外部 Agent

```
GET /api/assistant/notifications/pending
    → 取出 pending 通知 → 自行投递（邮件/钉钉等）
    → POST .../ack 标记已投递
```

### 路径 C：iLink 即时推送

命中关键词后，若 `push_target == "ilink"`，调用 `ilink_push.send_message()` 推送到微信私聊。推送结果通过 WebSocket 广播到前端。

## 通知生命周期

```
pending（待投递）
    ├── ack() → delivered（已投递）
    ├── ignore() → ignored（已忽略）
    └── 超 retention_hours → cleanup_expired() 自动删除（仅清理非 pending）
```

## 数据去重

前端点击"添加提醒群"时，若选择的 chat_id 已存在于某现有 AlertGroup，则自动合并关键词（去重），不创建重复行。编辑时若 chat_id 改为另一个已存在组的 ID，同样合并并移除被编辑行。

## 代码位置

| 组件 | 文件 |
|------|------|
| AlertEngine | `src/assistant/alert.py` |
| AlertGroup dataclass | `src/assistant/config.py` |
| Outbox | `src/assistant/outbox.py` |
| 前端 AlertGroupCard / Editor | `ui/src/components/AssistantPanel.jsx` |
| 首页 Dashboard | `ui/src/components/Dashboard.jsx` |