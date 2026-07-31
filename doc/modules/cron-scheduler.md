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
| 防重触 | 内存（重启即失） | 持久化 + 启动 3h 追赶机制 |
| 配置热更新 | 读盘 reload | `update_config()` 支持 |
| 静默语义 | `[SILENT]` 完全静默（不推送不记录） | 摘要"无新内容"仍写 Outbox（记录但不推送） |

两者都由 bot 启动时创建并 `start()`，并行运行。前端「定时任务」面板管理 CronScheduler 的任务；Dashboard 的 `/api/scheduled-tasks` 把两者配置合并成只读概览。

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
