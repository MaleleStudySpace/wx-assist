# Skill 体系

## 1. 功能定位

Skill 是一个可复用的能力单元，既可以被通用定时任务调用，也可以被 Agent 即时执行。定义文件位于本地 Skill 目录，使用 YAML frontmatter 描述元数据。

## 2. 类型

### script

执行本地脚本，不需要 AI。适合天气、数据整理等有明确输入输出的任务。

### prompt

把指令交给 Agent 一次性执行，需要 AI。适合需要自然语言理解、工具组合或内容整理的任务。

## 3. 定义示例

```yaml
---
name: weather
description: 查询指定城市天气
type: script
command: weather.py
timeout: 45
args:
  location:
    type: string
    required: true
  days:
    type: integer
    default: 2
---
```

prompt 类型需要 `prompt` 字段，不需要 `command`。

## 4. 执行约定

```text
SkillEngine.execute(name, args)
  ├── script → 子进程 → 返回 stdout
  └── prompt → Agent.run_once() → 返回文本
```

`[SILENT]` 表示没有新内容：CronScheduler 会跳过推送。真实错误必须返回错误说明，不能用 `[SILENT]` 掩盖。

## 5. 使用入口

- 定时任务页面：创建 Cron 任务并选择 Skill；
- Agent 工具：列出、执行或创建 Skill；
- API：查看 Skill 列表或生成示例，生成示例使用 `POST /api/skills/sample`。

## 6. 安全边界

Skill 脚本运行在本地进程中。创建和修改 Skill 属于写操作，应由 Agent 请求用户确认。不要在 Skill 中写入 API Key、账号凭据或个人路径。

## 7. 代码位置

- 引擎：`src/skill/engine.py`
- Cron：`src/scheduler/cron_scheduler.py`
- Agent 工具：`src/agent/tools.py`
- API：`src/web/api_handlers.py`
- 前端：`ui/src/components/SkillLibrary.jsx`
