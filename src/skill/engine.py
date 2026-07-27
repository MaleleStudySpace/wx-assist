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
        elif skill_type == "agent":
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

        # 子进程强制 UTF-8（Windows GBK 下中文/特殊字符不会炸）
        import os as _os
        result = subprocess.run(
            [sys.executable, str(script_path)] + cmd_args,
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
            "你是定时任务助手。根据用户的 prompt 执行任务。"
            " 如果需要外部数据，可以调用提供的工具。"
            " 如果没有新内容需要推送，请只回复 [SILENT]。"
            " 其他情况正常输出推送内容。"
        )

        logger.info("[SKILL] exec agent: %s", meta.get("name"))
        return self._agent_engine.run_once(prompt, system_override=system)
