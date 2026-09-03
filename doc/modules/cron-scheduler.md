# 通用定时任务（CronScheduler）

## 一句话说明

面向任意 **Skill** 的通用 cron 调度引擎：任务只引用 skill 名 + cron 表达式 + 参数，到点执行 skill，结果写入任务中心和通知队列，可选推送微信。与面向群聊/公众号摘要的旧 DigestScheduler 并行运行、互不替代。

## cron 语法（`src/utils/cron.py`）

5 字段标准 cron：`分 时 日 月 周`，**周日 = 0**。

| 语法 | 示例 | 说明 |
|------|------|------|
| `*` | `* * * * *` | 每分 |
| 整数 | `0 8 * * *` | 每天 8 点 |
| 范围 | `0 9 * * 1-5` | 工作日 9 点 |
| 步进 | `*/15 * * * *` | 每 15 分钟 |
| 列表 | `0 9 * * 1,3,5` | 周一/三/五 9 点 |
| 多行 | `0 9 * * 1-5\n30 18 * * 1-5` | 多个触发时间（任一匹配即触发） |

不支持 `?` / `L` / `W` 扩展语法。反向范围（如 `5-1`）判为非法。

配套校验：
- `validate_cron_syntax(expr)` — 纯语法校验（API 创建与 Agent `create_cron` 均调用）
- `validate_daily_cron(expr)` — 日定时领域校验（OA / 群聊摘要用，分时须单整数）
- 前端 `ui/src/utils/cron.js` — 与后端逐字段对齐的校验副本 + 未来 3 次触发时间计算

## 任务结构（`data/cron_jobs.json`）

```json
{
  "jobs": [
    {
      "name": "牡丹江天气日报",
      "skill": "weather",
      "cron": "0 8 * * *",
      "args": {"location": "牡丹江", "days": 2},
      "push": {"enabled": true, "target": "ilink"},
      "id": "d8ae7b2773c0",
      "enabled": true,
      "created_at": "2026-07-31T08:00:00",
      "last_run": null,
      "status": "idle",
      "run_count": 0,
      "error_count": 0
    }
  ]
}
```

写盘用原子方式（先 `.tmp` 再 `replace`），防写半截崩溃。

## 执行链路

```
CronScheduler._run（daemon 线程，60s tick）
    │  _tick():
    │    enabled? → cron_matches(expr, now)? → 距上次 ≥ 120s（防重触）?
    │    命中 → ThreadPoolExecutor(3) 异步执行（不阻塞 tick）
    ▼
_execute_and_push(job)
    1. TaskCenter 记账（create_task → running）
    2. skill_engine.execute(skill_name, args)
        └─ 不关心 skill 是 script 还是 ai，统一执行
        ├─ 成功 → complete_task(result)
        ├─ 异常 → fail_task(error) + text = "[CRON 错误] {name}: {e}"
        │         （报错计入 error_count，前端"⚠ 有失败"筛选）
        └─ [SILENT] → 记日志跳过推送，result 记空串
    3. 回写 job：last_run / run_count / error_count
    4. push.enabled? → outbox.add(notif_type="cron")
        └─ target == "ilink" → iLink 推送 + 推送结果写回 outbox 与 TaskCenter
```

### 关键约定

| 项目 | 约定 |
|------|------|
| tick 间隔 | 60s（1s 粒度 sleep，便于快速响应 stop） |
| 防重触 | 同一任务 2 分钟（内存态，重启即失） |
| [SILENT] | `text.strip() == "[SILENT]"` → 跳过推送（仅限"内容无变化"场景） |
| 报错 | 必须推送告知用户，**禁止**用 [SILENT] 掩盖；错误计入 error_count |
| 并发 | ThreadPoolExecutor(max_workers=3) + Lock 保护任务表 |

## 手动触发

`POST /api/scheduler/tasks/<id>/run`（`run_now`）与 tick 共用 `_execute_and_push`，走完整执行 + 推送链路，前端"立即执行"按钮调用后预览前 120 字结果。

## 与 DigestScheduler 的分工

| 维度 | CronScheduler（新） | DigestScheduler（旧） |
|------|---------------------|----------------------|
| 触发对象 | 任意 skill（script / ai） | 群聊摘要 `digest_groups` / 公众号摘要 `oa_groups` |
| 任务类型 | `cron` | `group_digest` / `oa_digest` |
| 配置存储 | `data/cron_jobs.json` | `data/assistant_config.json` |
| 防重触 | 内存（重启即失） | 持久化到 `data/scheduler_state.json` + 启动 3h 追赶机制 |
| 配置热更新 | 读盘 reload | `update_config()` 支持 |
| 静默语义 | `[SILENT]` 完全静默（不推送不记录） | 摘要"无新内容"仍写 Outbox（记录但不推送） |

两者都由 bot 启动时创建并 `start()`，并行运行。前端「定时任务」面板管理 CronScheduler 的任务；Dashboard 的 `/api/scheduled-tasks` 把两者配置合并成只读概览。

### DigestScheduler 的 `last_triggered` key 体系

| 任务 | key |
|------|-----|
| 群聊摘要分组 | `dg:{DigestGroup.id}` |
| 公众号摘要分组 | `oa:{OAGroup.id}` |

群摘要的 key 曾经是 `dg.chat_id or dg.group_name`。分组模型改造后换成 `dg:{id}`，
`_migrate_state_keys()` 在 `DigestScheduler.__init__` 里（`start()` 之前）自动迁移旧 key
并清理所有非 `dg:` / `oa:` 前缀的残留项。

**为什么必须迁移**：旧 key 全部失配 → `_catch_up_missed_crons` 认为每个组"从没触发过" →
在 3 小时窗口内把匹配过的 cron 全部补触发一轮（线上 3 个组 = 升级后立刻收到 3 条重复摘要推送）。
迁移要点：

- 多个旧 key 命中同一组时取 **`max(stamps)`**（最近一次），拿更早的时间戳会误判成"错过了"
- 迁移后**立即 `_save_state`**，不等 `_tick` 末尾 —— 进程若在追赶之后、`_tick` 之前崩溃，
  下次启动读到的仍是旧 key，会再补触发一轮
- 幂等：`dg:{id}` 已存在时直接短路

`update_config()` 里对 state 中**首次出现**的组 `setdefault(f"dg:{id}", now)`：热更新时
"state 里没有的组"就是用户刚在网页端新建的，不打时间戳会被紧接着的 `_tick` 当成
"错过了一次"而立刻补触发。这个 `setdefault` **刻意只放在 `update_config`、不放在 `__init__`** ——
放在 `__init__` 会让 `_catch_up_missed_crons` 永久失效，而它的存在意义正是补上重启期间错过的 cron
（`update_config` 的调用方只有 agent 工具、OA 分组 CRUD 和 WebUI PUT，启动路径不经过它）。

## 后端 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/scheduler/tasks` | GET / POST | 列出 / 创建任务 |
| `/api/scheduler/tasks/<id>` | PUT / DELETE | 更新 / 删除 |
| `/api/scheduler/tasks/<id>/run` | POST | 立即执行 |

## 代码位置

| 组件 | 文件 |
|------|------|
| CronScheduler | `src/scheduler/cron_scheduler.py` |
| cron 匹配 + 校验 | `src/utils/cron.py` |
| TaskCenter | `src/assistant/task_center.py` |
| Agent cron 工具 | `src/agent/tools.py` |
| 前端定时任务面板 | `ui/src/components/SchedulerPanel.jsx` |
| 前端任务中心 | `ui/src/components/TaskCenter.jsx` |
