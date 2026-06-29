# 公众号助手（OA Assistant）

## 一句话说明

将关注的公众号按主题分组，AI 定时或手动生成摘要，支持预设模板和完全自定义 prompt。同时提供新文章即时提醒能力。

## 功能拆分

公众号助手包含两个独立子系统：

| 子系统 | 触发方式 | 用途 |
|--------|----------|------|
| OA 定时摘要 | 定时 cron / 手动触发 | 按分组汇总新文章，AI 生成结构化摘要 |
| OA 即时提醒 | 后台 60s 轮询 | 监控公众号，新文章发布即刻推送通知 |

两个子系统共享 OA 文章解析引擎，但调度和去重机制各自独立。

## OA 定时摘要

### 配置字段（OAGroup）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | str | "" | 唯一 ID（自动生成） |
| `name` | str | "" | 分组显示名 |
| `accounts` | list[str] | [] | 公众号列表（gh_xxx） |
| `cron_expr` | str | "" | 5 字段 cron 表达式（多行支持） |
| `digest_template` | str | "default" | 摘要模板 key |
| `push_target` | str | "" | "ilink"=推送到微信 |
| `lookback_hours` | int | 24 | 回溯窗口 |
| `lookback_mode` | str | "auto" | auto=从 schedule 推导；manual=直接使用 lookback_hours |
| `custom_prompt` | str | "" | 自定义 prompt（替代模板） |
| `enabled` | bool | True | 主开关 |

### 数据流

```
触发摘要（手动 / 定时 cron 匹配）
    │
    ▼
1. 遍历 group.accounts[]（gh_xxx 列表）
2. 从本地数据库查询每号最近文章
3. 解析 → zstd 解压 → XML 提取 title/url/digest/cover
4. 时间窗口过滤 + URL 去重（DigestHistory）
5. 可选全文抓取（HTTP GET）→ HTML 清洗 → 截断 8000 字
6. Prompt 组装：
   system = custom_prompt? or DIGEST_TEMPLATES[key]
   user   = "请总结以下 N 篇文章：\n---\n### {标题}\n来源: {xxx}\n\n{内容}\n\n链接: {url}"
7. call_llm() → AI 生成摘要
8. DigestHistory.mark_digested() → 标记已摘要
9. outbox.add(notif_type="oa_digest")
10. push_target=="ilink"? → iLink 推送
11. WebSocket 广播 oa_digest_progress
```

### 摘要模板

| key | 名称 | 风格 |
|-----|------|------|
| `default` | 通用摘要 | 核心要点 + 关键信息 + 简评 |
| `tech` | 技术深度 | 核心技术 + 实现细节 |
| `entertainment` | 娱乐速览 | 核心事件 + 关键人物 |
| `business` | 商业分析 | 数据 + 市场影响 + 投资信号 |
| `news` | 新闻报道 | 5W1H + 关键数据 |
| `custom` | 自定义 | 完全自定义 prompt |

模板作为 system prompt 发送给 LLM。选择 custom 时，替换为用户的自定义指令。

### 去重（DigestHistory）

URL 级别的去重，持久化到 `data/oa_digest_history.json`。已摘要的文章在后续运行中跳过，30 天自动清理过期记录。

### 时间窗口

- **auto 模式**：解析 cron 表达式的小时字段，计算相邻执行间隔 + 1h buffer
- **manual 模式**：直接使用 `lookback_hours`

## OA 即时提醒

独立的后台 daemon 线程（`OAMonitorEngine`），60s 轮询所有被监控的公众号，发现新文章即推通知。

### 数据流

```
OAMonitorEngine daemon 线程（60s 轮询）
    │
    ▼
对于每个启用的 monitor_group：
    遍历 group.accounts[]（gh_xxx 列表）
        │
        ▼
    fetch_oa_articles(client, gh_id, limit=10)
        │
        ▼
    时间过滤（仅 5 分钟内发布的新文章）
    URL 去重（内存 Set，7 天自动清理）
        │
        ▼
    AI 摘要（可选，失败降级为标题摘要）
        │
        ▼
    outbox.add(notif_type="oa_article_alert", priority="high")
        │
        ├── push_target == "ilink"? → iLink 推送
        └── 仅入队
```

### 与定时摘要的区别

| 维度 | 定时摘要 | 即时提醒 |
|------|----------|----------|
| 调度 | cron 定时 / 手动 | 60s 后台轮询 |
| 范围 | 分组内所有号的历史文章 | 仅最新发布文章 |
| 去重 | DigestHistory（URL 持久化，30 天） | `_alerted_urls`（内存 Set，7 天） |
| AI 摘要 | 必选 | 可选（失败降级） |
| 推送频率 | 按计划 | 实时 |

## 代码位置

| 组件 | 文件 |
|------|------|
| OADigestService | `src/assistant/oa_digest.py` |
| OAMonitorEngine | `src/assistant/oa_monitor.py` |
| OAGroupManager | `src/assistant/oa_groups.py` |
| 文章解析 | `src/assistant/oa_parser.py` |
| 全文抓取 | `src/assistant/oa_reader.py` |
| 分组配置 | `src/assistant/config.py` |
| 前端 OATab | `ui/src/components/OATab.jsx` |
| 前端 Dashboard | `ui/src/components/Dashboard.jsx` |