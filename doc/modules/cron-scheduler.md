# 通用定时任务

## 1. 功能定位

CronScheduler 面向任意 Skill，任务只引用 Skill 名称、cron 表达式和参数。群聊/公众号摘要由独立的 DigestScheduler 管理，两者并行运行。

## 2. cron 语法

支持 5 字段：分、时、日、月、周；周日为 0。

```text
0 8 * * *       每天 8 点
0 9 * * 1-5     工作日 9 点
*/15 * * * *    每 15 分钟
```

支持范围、列表、步进和多行表达式，不支持 `?`、`L`、`W` 扩展语法。前后端分别校验表达式，错误会直接返回给用户。

## 3. 任务执行

```text
每分钟检查
  → enabled + cron 匹配 + 防重复触发
  → 线程池执行 Skill
  → TaskCenter 记录 running/completed/failed
  → Outbox 记录结果
  → [SILENT] 跳过通知
  → 其他结果投递到已绑定渠道
```

任务失败会增加错误计数并保留可读错误，不得用 `[SILENT]` 隐藏异常。

## 4. 任务字段

包括名称、Skill、cron、参数、启用状态、推送开关、上次运行时间、运行次数和错误次数。任务保存于本地运行数据中，不应在公开文档中填写真实任务或个人路径。

## 5. Skill 类型

- `script`：执行本地脚本，不需要 AI；
- `prompt`：调用 Agent 一次性执行，需要 AI。

例如天气查询属于 script Skill；公众号 Markdown 归档属于 prompt Skill。

## 6. 与摘要调度器的区别

| 项目 | CronScheduler | DigestScheduler |
|---|---|---|
| 任务对象 | 任意 Skill | 群聊/公众号摘要分组 |
| 配置 | 通用 Cron 任务 | Assistant 配置 |
| AI 依赖 | 由 Skill 决定 | 摘要生成需要 |
| 静默 | `[SILENT]` 完全跳过通知 | 无新内容会保留执行记录 |

## 7. 代码位置

- 调度器：`src/scheduler/cron_scheduler.py`
- cron 校验：`src/utils/cron.py`
- Skill：`src/skill/engine.py`
- 前端：`ui/src/components/SchedulerPanel.jsx`
