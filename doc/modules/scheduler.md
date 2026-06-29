# 调度器

## 一句话说明

项目有两套调度器，配置同一来源（`data/assistant_config.json`），但调度引擎和职责不同。

| 维度 | DigestScheduler | TaskScheduler |
|------|----------------|---------------|
| 文件 | `src/assistant/scheduler.py` | `src/scheduler/task_scheduler.py` |
| 引擎 | 自定义 daemon 线程 + 60s 轮询 | APScheduler BackgroundScheduler |
| Cron 解析 | 自实现 `_cron_matches()` | APScheduler CronTrigger |
| 触发方式 | cron_expr 或 HH:MM 列表 | 仅 cron_expr |
| 范围 | 群聊摘要 + 公众号摘要 | 公众号摘要（备用）、收藏导出 |

两个调度器无 import 关系，完全独立。

## DigestScheduler

Bot 启动时随 Assistant 子系统一并注册的 `daemon=True` 线程。

### 生命周期

```
bot.py → assistant_enabled? → DigestScheduler(config, outbox, summarizer, store)
    │
    ▼
scheduler.start()
    │  检查 assistant_enabled + 至少一个 enabled digest/OA group
    │  启动 daemon 线程 "digest-scheduler"
    ▼
_run() → 无限循环 _tick() → 每 60s
    │  遍历 digest_groups + oa_groups
    │  _should_trigger() / _cron_matches()
    │  MIN_TRIGGER_GAP_SEC=120 防重复触发
    ▼
_generate_digest(dg)    # 群聊摘要
_generate_oa_digest(oa) # 公众号摘要
    ▼
update_config() 支持热更新（不停线程直接替换 config 引用）
    │  收到 PUT /api/assistant/config 时调用
    │  检测 assistant_enabled toggle 自动 start/stop
```

### Cron 约定

`DigestScheduler` 的 cron 解析遵循固定规则：

- 多行格式，每行一个触发时间
- 每行 5 字段：`分 时 日 月 周`
- 分/时：单个整数（不支持逗号、`*`、范围、步进）
- 日/月：固定为 `*`
- 周：`*` 或范围 `1-5` 或列表 `1,3,5`
- 支持步进（`*/15`）和范围（`1-5`）用于分/时字段

前后端共用一个校验函数（`_validate_cron_expr()`），保证配置一致性。

### 热更新

`DigestScheduler.update_config()` 直接替换内部 config 引用：
- 摘要在下次 `_tick()`（60s 内）自动使用新配置
- `assistant_enabled toggle` 触发 start/stop 线程
- 不中断正在执行的摘要生成

## TaskScheduler

基于 APScheduler 的通用调度器，OA 摘要和收藏导出按需创建实例。

### 数据模型

```python
@dataclass
class SchedulerTask:
    id: str              # 唯一 ID
    name: str            # 人类可读名称
    task_type: str       # "oa_digest" | "fav_export"
    cron_expr: str       # 5 字段 cron
    function_ref: str    # 点分导入路径
    enabled: bool
    last_run_time: str   # ISO-8601
    status: str          # "idle" | "running" | "error"
```

### 执行流程

```
API 请求 → 创建 TaskScheduler 实例
    │
    ▼
load_tasks() → 从 assistant_config.json 读取
    │
    ▼
add/schedule → 加入 APScheduler
    │
    ▼
_run_task(task_id)
    │  _resolve_callable(function_ref) → importlib.import_module()
    │  调用 → 更新 status + last_run_time
    ▼
stop() → APScheduler shutdown
```

## 代码位置

| 组件 | 文件 |
|------|------|
| DigestScheduler | `src/assistant/scheduler.py` |
| TaskScheduler | `src/scheduler/task_scheduler.py` |
| SchedulerTask / 配置 | `src/assistant/config.py` |
| Bot 注册/热更新 | `src/bot.py`、`src/web/server.py` |