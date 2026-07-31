# Skill 体系

## 一句话说明

Skill = 一个可执行的能力单元，存储在 `data/skills/{name}/SKILL.md`。通过解析 YAML frontmatter 决定执行方式（脚本子进程或 AI 生成）。同一套 Skill 可被定时任务（CronScheduler）和 AI 助手（Agent）调用，统一执行路径。

## 目录结构

```
data/skills/{name}/
    ├── SKILL.md                 ← 元数据定义（YAML frontmatter）
    ├── scripts/                 ← script 类型的脚本目录
    └── examples/                ← 可选：使用示例
```

## SKILL.md 格式

```yaml
---
name: weather                 # skill 唯一标识（缺省用目录名）
description: 天气预报 — 查询指定城市的天气情况
type: script                  # "script" | "agent" | "ai"（agent 与 ai 等价）
command: weather.py           # script 类型必填，相对 scripts/ 的脚本文件名
timeout: 45                   # 超时秒数（默认 30，仅 script 类型生效）
args:
  location:
    type: string              # 参数类型（string/integer/number/boolean）
    required: true            # 是否必填
    description: 城市名，如 北京、上海、Tokyo
  days:
    type: integer
    default: 2
    description: 预报天数（1-3）
---
```

- ai 类型必填 `prompt` 字段（AI 执行指令），无 `command`
- `timeout` 字段只对 script 类型生效（子进程超时），ai 类型不消费

## 执行分派（`SkillEngine.execute()`）

```
execute(name, args)
    │
    ├─ type == "script" → _execute_script（子进程）
    └─ type == "agent"/"ai" → _execute_agent（AI 生成）
```

返回值约定：`"[SILENT]"` 表示无新内容（见下文 [SILENT] 协议）。

## script 类型执行

```
1. 定位脚本：data/skills/{name}/scripts/{command}
             找不到 → 回退 data/scripts/{command}
2. 参数转换：args 逐项转 --key value
             True → 只传 --key；False → 跳过
3. subprocess.run:
   - 解释器 = sys.executable（EXE 模式下前置 --run-script 参数，
     由 desktop.py 检测后直接执行脚本并退出，避免拉起第二个实例）
   - encoding="utf-8", errors="replace"
   - env 注入 PYTHONIOENCODING=utf-8 + PYTHONUTF8=1（Windows GBK 防乱码）
   - Windows 下 CREATE_NO_WINDOW 防黑窗
   - timeout = meta["timeout"]
4. returncode != 0 → 抛 SkillError（stderr 优先，回退 stdout 前 200 字）
5. 返回 stdout
```

## ai 类型执行

```
_execute_agent(meta, args)
    │
    ▼
prompt = meta["prompt"]（必填，缺省报错）
    │  追加 "## 参数" JSON 块（ensure_ascii=False 中文可读）
    ▼
agent_engine.run_once(prompt, system_override=system)
    │  一次性执行：不装历史、不触发记忆合并
    │  与 run() 共用同一 ReAct 循环与同一套工具集（含 MCP 注入）
    ▼
返回 LLM 输出文本
```

ai 类型的 system prompt 固定为：

> 根据用户的需求执行任务。如果需要外部数据，可以调用提供的工具。
> **如果确认没有任何新内容可输出（如监控类任务无变化），只回复 [SILENT]。
> 如果执行失败或遇到错误，必须输出错误说明，禁止用 [SILENT] 掩盖。**
> 其他情况正常输出结果。

## [SILENT] 协议

| 项目 | 约定 |
|------|------|
| 常量 | `[SILENT]`（`cron_scheduler.py`） |
| 判定 | 输出文本 `strip()` 后恰好等于 `[SILENT]` |
| 效果 | CronScheduler 跳过推送；TaskCenter 结果记为空串 |
| 适用 | 监控/聚合类任务"内容无变化"的场景 |

**错误禁止静默**：script 类型报错应 `print(错误信息); sys.exit(1)`（非零退出码让调度器记 `error_count` 并推送错误）；ai 类型报错必须输出错误说明。内置示例 weather 脚本即按此约定实现（失败重试 2 次后输出错误并退出）。

## 内置示例（`create_sample_skills()`）

| skill | 类型 | 功能 |
|-------|------|------|
| `weather` | script | 天气预报 — 调 wttr.in（免费零配置），失败重试 2 次，报错推送 |
| `skill-designer` | ai | 技能设计助手 — 分析需求输出设计方案，引导用户确认后由助手创建 |

可通过 `POST /api/skills?sample=1` 一键生成示例。用户也可在微信里让 Agent 创建自定义 skill（见下）。

## 使用场景

### 1. 定时调度（CronScheduler）

前端「定时任务」面板配置任务，引用 skill 名 + cron 表达式 + 参数：

```
CronScheduler 到点触发 → SkillEngine.execute(name, args)
    │  [SILENT] → 跳过推送
    │  报错     → 记 error_count + 推送错误
    ▼
Outbox + 可选 iLink 推送
```

### 2. AI 助手（Agent 工具链）

Agent 通过三个 skill 工具操作：

| 工具 | 需确认 | 用途 |
|------|:------:|------|
| `list_skills` | ❌ | 列出所有可用 skill 及其参数 |
| `execute_skill` | ❌ | 立即执行一个 skill |
| `create_skill` | ✅ | 创建 AI 类型 skill（名称 2-32 字符，字母数字下划线） |

推荐流程：用户描述需求 → Agent 调 `skill-designer` 设计方案 → 用户确认 → Agent 调 `create_skill` 正式创建。

## 后端 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/skills` | GET | 列出所有 skill 元数据 |
| `/api/skills?sample=1` | POST | 生成两个示例 skill（weather + skill-designer） |

## 代码位置

| 组件 | 文件 |
|------|------|
| SkillEngine | `src/skill/engine.py` |
| CronScheduler | `src/scheduler/cron_scheduler.py` |
| Agent skill 工具 | `src/agent/tools.py` |
| skill API | `src/web/api_handlers.py` |
| 前端 Skill 库 | `ui/src/components/SkillLibrary.jsx` |
| 前端定时任务面板 | `ui/src/components/SchedulerPanel.jsx` |
