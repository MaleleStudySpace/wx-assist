"""Skill 引擎 — 加载并执行 skill 定义。

Skill = 一个可执行的能力单元，存储在 data/skills/{name}/SKILL.md 中。
通过解析 YAML frontmatter 确定执行方式（脚本或 AI）。

目录结构：
  data/skills/{name}/
    ├── SKILL.md                 ← 元数据定义
    ├── scripts/                 ← script 类型的脚本目录
    └── examples/                ← 可选：使用示例

同时被 CronScheduler 和 AgentEngine 调用，统一执行路径。
"""

import json
import logging
import os
import subprocess
import sys
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_DIR = Path("data/skills")


class SkillNotFound(FileNotFoundError):
    pass


class SkillError(RuntimeError):
    pass


def _parse_skill_file(path: Path) -> dict:
    """解析 SKILL.md 文件，返回 YAML frontmatter 字典。"""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise SkillError(f"SKILL.md 必须以前置 --- 开头: {path}")
    parts = raw.split("---", 2)
    if len(parts) < 3:
        raise SkillError(f"SKILL.md 格式错误（缺少闭合 ---）: {path}")
    frontmatter = yaml.safe_load(parts[1])
    if not isinstance(frontmatter, dict):
        raise SkillError(f"SKILL.md 前置元数据解析失败: {path}")
    return frontmatter


class SkillEngine:
    """Skill 加载与执行引擎。

    Args:
        agent_engine: AgentEngine 实例（agent 类型 skill 需要）。
    """

    def __init__(self, agent_engine=None):
        self._agent_engine = agent_engine

    def list_skills(self) -> list[dict]:
        """列出所有可用 skill 的元数据。"""
        if not SKILL_DIR.exists():
            return []
        skills = []
        for child in sorted(SKILL_DIR.iterdir()):
            skill_md = child / "SKILL.md"
            if not child.is_dir() or not skill_md.exists():
                continue
            try:
                meta = _parse_skill_file(skill_md)
                skills.append({
                    "name": meta.get("name", child.name),
                    "description": meta.get("description", ""),
                    "type": meta.get("type", "script"),
                    "args": meta.get("args", {}),
                })
            except Exception as e:
                logger.warning("[SKILL] 解析失败 %s: %s", child.name, e)
        return skills

    def get_skill(self, name: str) -> dict:
        """加载并返回单个 skill 的元数据。"""
        skill_dir = SKILL_DIR / name
        path = skill_dir / "SKILL.md"
        if not path.exists():
            raise SkillNotFound(f"Skill 不存在: {name}")
        meta = _parse_skill_file(path)
        meta.setdefault("name", name)
        meta.setdefault("type", "script")
        # 记录 skill 目录路径，给执行方法用
        meta["_skill_dir"] = str(skill_dir)
        return meta

    def execute(self, name: str, args: dict = None) -> str:
        """执行一个 skill，返回输出文本。

        Args:
            name: skill 名称（对应 data/skills/{name}/SKILL.md）。
            args: 参数字典。script 类型的参数传给脚本，
                  agent 类型作为 prompt 上下文。

        Returns:
            输出文本。"[SILENT]" 表示无新内容。

        Raises:
            SkillNotFound: skill 文件不存在。
            SkillError: 执行失败。
        """
        meta = self.get_skill(name)
        skill_type = meta.get("type", "script")
        args = args or {}

        if skill_type == "script":
            return self._execute_script(meta, args)
        elif skill_type in ("agent", "ai"):
            return self._execute_agent(meta, args)
        else:
            raise SkillError(f"不支持的 skill 类型: {skill_type}")

    def _scripts_dir(self, meta: dict) -> Path:
        """返回 skill 的 scripts 目录。"""
        sd = Path(meta.get("_skill_dir", ""))
        return sd / "scripts"

    def _execute_script(self, meta: dict, args: dict) -> str:
        """执行脚本类型的 skill。脚本在 skill 自带的 scripts/ 目录下。"""
        command = meta.get("command", "")
        if not command:
            raise SkillError(f"Skill '{meta.get('name')}' 未指定 command")

        timeout = meta.get("timeout", 30)

        # 解析脚本路径：优先 skill 自带的 scripts/ 目录
        script_path = self._scripts_dir(meta) / command
        if not script_path.exists():
            # 兼容：在脚本目录找不到时，也查一遍 data/scripts/
            alt_path = Path("data/scripts") / command
            if alt_path.exists():
                script_path = alt_path
            else:
                raise SkillError(f"脚本未找到: {command}")

        logger.info("[SKILL] exec script: %s %s", script_path, args)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        # 构建命令行参数
        cmd_args = []
        for k, v in args.items():
            if v is True:
                cmd_args.append(f"--{k}")
            elif v is False:
                pass
            else:
                cmd_args.extend([f"--{k}", str(v)])

        # 决定可执行文件和前置参数
        # 在 PyInstaller EXE 中 sys.executable 指向 exe 本身，
        # 需传 --run-script 让 desktop.py 跳过互斥锁检查，
        # 直接把脚本跑完就退出。
        if getattr(sys, "frozen", False):
            executable = sys.executable
            base_args = ["--run-script", str(script_path)]
        else:
            executable = sys.executable
            base_args = [str(script_path)]

        # 子进程强制 UTF-8（Windows GBK 下中文/特殊字符不会炸）
        import os as _os
        result = subprocess.run(
            [executable] + base_args + cmd_args,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            env={**_os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            raise SkillError(
                f"脚本退出码 {result.returncode}: {stderr or stdout[:200]}")
        return stdout

    def _execute_agent(self, meta: dict, args: dict) -> str:
        """执行 AI 类型的 skill。"""
        if not self._agent_engine:
            raise SkillError("agent 类型 skill 需要 agent_engine，但未注入")

        prompt = meta.get("prompt", "")
        if not prompt:
            raise SkillError(f"Skill '{meta.get('name')}' 未指定 prompt")

        if args:
            prompt += "\n\n## 参数\n" + json.dumps(args, ensure_ascii=False, indent=2)

        system = (
            "根据用户的需求执行任务。"
            " 如果需要外部数据，可以调用提供的工具。"
            " 如果没有新内容需要输出，请只回复 [SILENT]。"
            " 其他情况正常输出结果。"
        )

        logger.info("[SKILL] exec agent: %s", meta.get("name"))
        return self._agent_engine.run_once(prompt, system_override=system)

    def _build_skill_dir(self, name: str) -> Path:
        """创建 skill 目录并返回路径。"""
        d = SKILL_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "scripts").mkdir(exist_ok=True)
        return d

    def _write_skill_md(self, path: Path, meta: dict):
        """写 SKILL.md（YAML frontmatter）。"""
        lines = ["---"]
        for k, v in meta.items():
            if k.startswith("_") or v is None:
                continue
            if isinstance(v, dict):
                lines.append(f"{k}:")
                for sk, sv in v.items():
                    lines.append(f"  {sk}: {json.dumps(sv, ensure_ascii=False)}")
            else:
                lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        lines.append("---")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def create_sample_skills(self) -> list[dict]:
        """创建两个示例 skill：weather（script）+ skill-creator（ai）。

        Returns:
            list[dict]: 创建的 skill 元数据列表。
        """
        created = []

        # ── 1. weather (script) ──────────────────────────────────────
        weather_meta = {
            "name": "weather",
            "type": "script",
            "description": "天气预报 — 查询指定城市的天气情况",
            "command": "weather.py",
            "timeout": 15,
            "args": {
                "location": {
                    "type": "string",
                    "required": True,
                    "description": "城市名，如 北京、上海、Tokyo",
                },
                "days": {
                    "type": "integer",
                    "default": 2,
                    "description": "预报天数（1-3）",
                },
            },
        }
        skill_dir = self._build_skill_dir("weather")
        self._write_skill_md(skill_dir / "SKILL.md", weather_meta)

        # 写 scripts/weather.py
        weather_py = r'''"""天气查询 — wttr.in，零配置免费，无需 API key。"""
import json, sys, urllib.request

def main():
    args = _parse_args()
    location = args.get("location", "北京")
    days = min(max(int(args.get("days", 2)), 1), 3)

    url = f"https://wttr.in/{urllib.request.quote(location)}?format=j1&lang=zh"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        print("[SILENT]")
        sys.exit(0)

    current = data["current_condition"][0]
    output = [
        f"\U0001f324 {location} 天气",
        "━" * 20,
        f"现在：{current['temp_C']}°C（体感 {current['FeelsLikeC']}°C）",
        f"状况：{current['lang_zh'][0]['value']}",
        f"湿度：{current['humidity']}% · 风速：{current['windspeedKmph']}km/h",
    ]

    for day in data["weather"][:days]:
        date = day["date"]
        hi = day["maxtempC"]
        lo = day["mintempC"]
        desc = day["hourly"][0]["lang_zh"][0]["value"]
        output.append(f"\n{date}：{desc} {lo}~{hi}°C")

    print("\n".join(output))

def _parse_args():
    args = {}
    i = 1
    while i < len(sys.argv):
        if sys.argv[i].startswith("--"):
            key = sys.argv[i][2:]
            val = sys.argv[i + 1] if i + 1 < len(sys.argv) else True
            args[key] = val
            i += 2
        else:
            i += 1
    return args

if __name__ == "__main__":
    main()
'''
        (skill_dir / "scripts" / "weather.py").write_text(weather_py, encoding="utf-8")
        created.append({"name": "weather", "type": "script", "description": weather_meta["description"]})
        logger.info("[SKILL] 示例 weather 已创建")

        # ── 2. skill-designer (ai) ──────────────────────────────────
        creator_meta = {
            "name": "skill-designer",
            "type": "ai",
            "description": "技能设计助手 — 帮你设计 ai 类型 skill 的方案",
            "timeout": 120,
            "args": {
                "idea": {
                    "type": "string",
                    "required": True,
                    "description": "描述你想创建的 skill 功能，越详细越好",
                },
            },
            "prompt": (
                "你是技能设计助手。用户会告诉你他想要一个什么样的 skill。\n"
                "你的任务：\n"
                "1. 分析用户需求，设计合理的 skill 名称、描述、prompt、参数\n"
                "2. 输出设计方案供用户审阅，明确提示用户确认后再创建（不要调用 create_skill 创建）\n\n"
                "输出格式（严格按照此格式，让后续流程能自动提取）：\n"
                "【设计方案】\n"
                "名称：xxx（英文，字母数字下划线 2-32 字符）\n"
                "描述：xxx\n"
                "指令：xxx（AI 执行时的完整步骤，务必写清楚）\n"
                "参数：xxx（如 {\"city\": {\"type\": \"string\", \"description\": \"城市名\"}}，无需参数就写无）\n\n"
                "注意：\n"
                "- 只设计 type: ai 的 skill\n"
                "- 指令要具体到执行步骤，不要笼统\n"
                "- 输出后请提示用户审阅，用户确认后才能创建"
            ),
        }
        skill_dir2 = self._build_skill_dir("skill-designer")
        self._write_skill_md(skill_dir2 / "SKILL.md", creator_meta)
        created.append({"name": "skill-designer", "type": "ai", "description": creator_meta["description"]})
        logger.info("[SKILL] 示例 skill-designer 已创建")

        return created

    def create_skill(self, name: str, description: str, prompt: str,
                     args_schema: dict = None) -> dict:
        """创建 ai 类型的 skill（供 create_skill 工具调用）。"""
        import re
        if not re.match(r'^[a-z0-9][a-z0-9_-]{1,31}$', name):
            raise SkillError(f"skill 名 '{name}' 非法：只能包含字母数字下划线，2-32 字符")

        skill_dir = SKILL_DIR / name
        if (skill_dir / "SKILL.md").exists():
            raise SkillError(f"Skill '{name}' 已存在")

        meta = {
            "name": name,
            "type": "ai",
            "description": description,
            "timeout": 60,
            "args": args_schema or {},
            "prompt": prompt,
        }
        self._write_skill_md(skill_dir / "SKILL.md", meta)
        logger.info("[SKILL] create_skill: %s", name)
        return {"name": name, "type": "ai", "path": str(skill_dir / "SKILL.md")}
