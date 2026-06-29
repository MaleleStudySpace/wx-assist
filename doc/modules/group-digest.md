# 群聊定时摘要（Group Digest）

## 一句话说明

按 cron 调度定时拉取群消息，AI 生成结构化摘要，写入通知队列并可选推送到微信，同时滚动更新群记忆。

## 数据流

```
用户在前端配置摘要群（群 / 时间 / 档案 / 推送）
    │
    ▼
AssistantConfig.digest_groups[] → data/assistant_config.json
    │
    ▼
DigestScheduler daemon 线程（60s 轮询）
    │  _should_trigger() → cron 或 HH:MM 匹配
    │  MIN_TRIGGER_GAP_SEC 去重
    ▼
_generate_digest(dg)
    1. 拉取回溯窗口内消息（limit=500）
    2. unread_only? → 切片到未读部分
    3. filter_messages(raw, ignore_kw) → 过滤噪音 + 媒体占位
    4. 构建 system_prompt + user_prompt → AI 调用
    5. generate_memory_update_prompt() → 更新 dg.memory
    6. outbox.add(notif_type="group_digest")
    7. push_target=="ilink"? → iLink 推送 + 广播推送结果
```

## 配置字段

### DigestGroup（`src/assistant/config.py`）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `chat_id` | str | "" | 群会话 ID |
| `group_name` | str | "" | 群显示名（选择群时自动填充） |
| `schedule` | list[str] | [] | HH:MM 触发时间列表（与 cron_expr 互斥，cron 优先） |
| `cron_expr` | str | "" | 5 字段 cron 表达式（多行，每行一个触发时间） |
| `lookback_hours` | int | 6 | 回溯窗口（3/6/12/24） |
| `enabled` | bool | True | 主开关 |
| `profile` | GroupProfile? | None | 群档案（群上下文） |
| `memory` | str | "" | 累积摘要记忆（自动更新，不可编辑） |
| `unread_only` | bool | False | 仅摘要未读消息 |
| `push_target` | str | "" | "ilink"=推到微信，""=仅入队 |

### GroupProfile（嵌套）

| 字段 | 类型 | 说明 |
|------|------|------|
| `summary` | str | 群简介 |
| `focus` | list[str] | 关注点 |
| `ignore` | list[str] | 忽略内容（同时用于 filter_messages） |
| `style` | str | 摘要风格预设："" / "行动项优先" / "完整复盘" / "极简速览" / "自定义" |
| `custom_prompt` | str | 自定义摘要指令 |

## Prompt 架构

### system_prompt 决策

```
有 custom_prompt?
  YES → system_prompt = custom_prompt（完全替代默认）
  NO  → system_prompt = DIGEST_SYSTEM_PROMPT
        + style 预设（行动项优先 / 完整复盘 / 极简速览）追加
```

- **custom_prompt 完全替代**默认 system prompt：用户获得对该群的完全控制权，仅影响该群。
- **style 预设追加**到默认 prompt 之后，是轻量风格调整。

### user_prompt 结构（`build_digest_prompt()`）

```
## 群信息
群简介 / 关注点 / 忽略内容

## 近期记忆
{memory 或 "（暂无历史记忆）"}

## 最近 N 条消息
[HH:MM] 昵称: 内容
...
```

只提供上下文，指令全部在 system_prompt 侧。

## 消息过滤

`filter_messages()` 处理两类问题：

1. **噪音过滤**：系统消息（入群/退群/改群名）、纯表情、极短消息、常见无意义回复（"收到"/"好的"/"ok" 等）、命中忽略关键词的消息。
2. **媒体占位**：图片/语音/视频/贴纸/应用消息等非文本类型，原始内容替换为结构化占位符（`{{ image }}` / `{{ voice }}` 等），让 LLM 知道上下文而不接触原始载荷。
3. **标识符清洗**：消息文本中的 `wxid_xxx` / `gh_xxx` 等内部标识符在进入 prompt 前被剥离，保证摘要只展示昵称。

> 这些处理同时服务于群摘要与关键词提醒（共用 `_strip_ids`），保证 LLM 输入与匹配/展示一致。

## 记忆更新

每次摘要后调用 `generate_memory_update_prompt()`，让 AI 在旧记忆基础上写一段 ≤500 字的新记忆，记录核心要点、近期趋势、群氛围。更新后立即 `save_assistant_config()` 持久化，下次摘要作为"近期记忆"注入 prompt，形成跨次记忆累积。

## 双通道输出

| 通道 | 路径 | 用途 |
|------|------|------|
| Outbox | `outbox.add(notif_type="group_digest")` | 持久化到 SQLite，供前端/外部 Agent 拉取 |
| iLink | `ilink_push.send_message()` | 即时推送到微信私聊（仅当 `push_target=="ilink"`） |

两条路径独立：iLink 推送失败不影响 Outbox 持久化；推送结果通过 WebSocket 广播到前端，UI 实时显示成功/失败提示。

## 关键设计决策

### 1. cron 多行格式

`cron_expr` 采用多行格式，每行一个触发时间（如 `0 9 * * 1-5\n30 18 * * 1-5`）。相比单行逗号表达式，多行更易读、不易因反复编辑而出错，前后端校验规则一致。配合前端时间芯片选择器，简单用户用 chip 生成 cron，高级用户可直接编辑。

### 2. schedule 与 cron_expr 共存

两字段都持久化，`cron_expr` 优先级更高。前端通过 `buildCronExpr()` / `parseCronExpr()` 双向同步。

### 3. custom_prompt 替代而非追加

群档案（focus/ignore/style）是客观上下文，不应被自定义指令覆盖；custom_prompt 是用户对该群摘要的"额外要求"，作为 system 输入完全替代默认指令，让用户获得完全控制权。

### 4. 风格预设作为 system 追加

style 预设是轻量调整（行动项优先 / 完整复盘 / 极简速览），追加到默认 system 之后，不替代默认角色定义。与 custom_prompt 互斥（选了自定义就不叠加预设）。

### 5. 无消息也入队

回溯窗口内无新消息时仍写一条 Outbox（标注"无新消息，摘要跳过"），让用户确认调度确实执行过，便于排查"为什么没收到摘要"。

## 代码位置

| 组件 | 文件 |
|------|------|
| DigestScheduler | `src/assistant/scheduler.py` |
| DigestGroup / GroupProfile | `src/assistant/config.py` |
| 过滤 + prompt 构建 + 记忆更新 | `src/assistant/digest.py` |
| Outbox | `src/assistant/outbox.py` |
| iLink 推送 | `src/wechat/ilink_push.py` |
| 前端 DigestGroupCard / Editor | `ui/src/components/AssistantPanel.jsx` |