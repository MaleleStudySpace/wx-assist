"""Assistant configuration — load/save data/assistant_config.json."""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("data/assistant_config.json")


def _validate_cron_expr(cron_expr: str, field_name: str = "cron_expr") -> str:
    """Validate cron against the fixed UI/backend contract.

    Returns an error message if invalid, empty string if valid.

    Fixed rule:
    - Multi-line allowed; one trigger time per line
    - Each line has exactly 5 fields: minute hour day month day_of_week
    - minute is a single integer 0-59
    - hour is a single integer 0-23
    - day and month must be '*'
    - day_of_week is '*' or comma/range expression with values 0-6
    """
    if not cron_expr:
        return ""

    lines = [line.strip() for line in cron_expr.strip().split('\n') if line.strip()]
    if not lines:
        return f"{field_name}: cron表达式不能为空"

    for i, line in enumerate(lines, start=1):
        parts = line.split()
        if len(parts) != 5:
            return f"{field_name}: 第{i}行必须有5个字段（分 时 日 月 周），当前={line}"
        minute, hour, day, month, dow = parts

        try:
            minute_i = int(minute)
            hour_i = int(hour)
        except ValueError:
            return f"{field_name}: 第{i}行分/时必须是单个数字，不能使用逗号、*、范围或步进"
        if not (0 <= minute_i <= 59):
            return f"{field_name}: 第{i}行分钟={minute} 超出范围，应为0-59"
        if not (0 <= hour_i <= 23):
            return f"{field_name}: 第{i}行小时={hour} 超出范围，应为0-23"

        if day != "*":
            return f"{field_name}: 第{i}行日字段必须是 *"
        if month != "*":
            return f"{field_name}: 第{i}行月字段必须是 *"

        if dow != "*" and not re.match(r'^(\d+(-\d+)?)(,\d+(-\d+)?)*$', dow):
            return f"{field_name}: 第{i}行周={dow} 格式错误，支持 *、1-5、1,2,3,4,5"

    return ""


@dataclass
class AlertGroup:
    chat_id: str = ""
    group_name: str = ""
    keywords: list[str] = field(default_factory=list)
    enabled: bool = True
    push_target: str = ""  # "" | "ilink"


@dataclass
class OAMonitorGroup:
    id: str = ""                                        # unique: "oam_001"
    name: str = ""                                      # display name
    accounts: list[str] = field(default_factory=list)   # gh_xxx list
    enabled: bool = True
    push_target: str = ""                               # "" | "ilink"


@dataclass
class GroupProfile:
    summary: str = ""          # 群简介（合并原 purpose + description）
    focus: list[str] = field(default_factory=list)    # 关注点
    ignore: list[str] = field(default_factory=list)   # 忽略内容
    style: str = ""            # 摘要风格预设: "" | "行动项优先" | "完整复盘" | "极简速览" | "自定义"
    custom_prompt: str = ""    # 额外摘要指令
    # Legacy fields (kept for backward compat during deserialization)
    purpose: str = ""          # DEPRECATED — merged into summary
    description: str = ""      # DEPRECATED — merged into summary


@dataclass
class DigestGroup:
    chat_id: str = ""
    group_name: str = ""
    schedule: list[str] = field(default_factory=list)  # ["12:00", "18:00"]
    cron_expr: str = ""                                  # 高阶: cron 表达式 (5字段), 与 schedule 互斥
    lookback_hours: int = 6
    enabled: bool = True
    profile: Optional[GroupProfile] = None
    memory: str = ""
    unread_only: bool = False   # 仅摘要未读消息
    push_target: str = ""       # 推送目标: "ilink" = 推到微信, "" = 不推送


@dataclass
class NotificationQueue:
    enabled: bool = True
    retention_hours: int = 24


@dataclass
class OAGroup:
    id: str = ""                                        # unique ID like "grp_001"
    name: str = ""                                      # display name
    accounts: list[str] = field(default_factory=list)   # gh_xxx list
    schedule: list[str] = field(default_factory=list)   # DEPRECATED: use cron_expr instead
    cron_expr: str = ""                                 # 5-field cron expression (same as DigestGroup)
    digest_template: str = "default"                    # prompt template key
    push_target: str = ""                               # chatroom or user wxid
    lookback_hours: int = 24                            # lookback window
    lookback_mode: str = "auto"                         # "auto" | "manual"
    custom_prompt: str = ""                             # custom prompt (overrides digest_template)
    enabled: bool = True


@dataclass
class FavExportConfig:
    enabled: bool = False
    output_dir: str = "data/fav_export"             # export directory
    formats: list[str] = field(default_factory=lambda: ["markdown", "json"])  # export formats
    last_export_timestamp: int = 0                  # for incremental export


@dataclass
class SchedulerTask:
    id: str = ""
    name: str = ""
    task_type: str = ""                             # "oa_digest", "fav_export", "group_digest"
    cron_expr: str = ""                             # cron expression
    ref_id: str = ""                                # reference to group or config
    function_ref: str = ""                          # dotted import path (aligned with ScheduledTask)
    enabled: bool = True
    last_run_time: str = ""                         # ISO-8601 (aligned with ScheduledTask)
    status: str = "idle"                            # "idle" | "running" | "error" (aligned with ScheduledTask)


@dataclass
class AssistantConfig:
    version: int = 1
    assistant_enabled: bool = False
    allow_wechat_send: bool = False
    alert_groups: list[AlertGroup] = field(default_factory=list)
    oa_monitor_groups: list[OAMonitorGroup] = field(default_factory=list)
    digest_groups: list[DigestGroup] = field(default_factory=list)
    notification_queue: NotificationQueue = field(default_factory=NotificationQueue)
    oa_groups: list[OAGroup] = field(default_factory=list)
    fav_export: FavExportConfig = field(default_factory=FavExportConfig)
    scheduler_tasks: list[SchedulerTask] = field(default_factory=list)


def _default_config() -> AssistantConfig:
    """Return a sensible default configuration."""
    return AssistantConfig(
        version=1,
        assistant_enabled=False,
        allow_wechat_send=False,
        notification_queue=NotificationQueue(enabled=True, retention_hours=24),
    )


def _config_to_dict(cfg: AssistantConfig) -> dict:
    """Serialize AssistantConfig to JSON-safe dict."""
    # Import the digest system prompt for the frontend to reference
    from .digest import DIGEST_SYSTEM_PROMPT, STYLE_PRESETS

    result = {
        "version": cfg.version,
        "assistant_enabled": cfg.assistant_enabled,
        "allow_wechat_send": cfg.allow_wechat_send,
        "alert_groups": [],
        "oa_monitor_groups": [],
        "digest_groups": [],
        "default_system_prompt": DIGEST_SYSTEM_PROMPT,
        "style_presets": STYLE_PRESETS,
        "notification_queue": {
            "enabled": cfg.notification_queue.enabled,
            "retention_hours": cfg.notification_queue.retention_hours,
        },
        "outbox_retention_hours": cfg.notification_queue.retention_hours,
        "oa_groups": [],
        "fav_export": {
            "enabled": cfg.fav_export.enabled,
            "output_dir": cfg.fav_export.output_dir,
            "formats": cfg.fav_export.formats,
            "last_export_timestamp": cfg.fav_export.last_export_timestamp,
        },
        "scheduler_tasks": [],
    }
    for ag in cfg.alert_groups:
        result["alert_groups"].append({
            "chat_id": ag.chat_id,
            "group_name": ag.group_name,
            "keywords": ag.keywords,
            "enabled": ag.enabled,
            "push_target": ag.push_target,
        })
    for omg in cfg.oa_monitor_groups:
        result["oa_monitor_groups"].append({
            "id": omg.id,
            "name": omg.name,
            "accounts": omg.accounts,
            "enabled": omg.enabled,
            "push_target": omg.push_target,
        })
    for dg in cfg.digest_groups:
        item = {
            "chat_id": dg.chat_id,
            "group_name": dg.group_name,
            "schedule": dg.schedule,
            "cron_expr": dg.cron_expr,
            "lookback_hours": dg.lookback_hours,
            "enabled": dg.enabled,
            "memory": dg.memory,
            "unread_only": dg.unread_only,
            "push_target": dg.push_target,
        }
        if dg.profile:
            item["profile"] = {
                "summary": dg.profile.summary,
                "focus": dg.profile.focus,
                "ignore": dg.profile.ignore,
                "style": dg.profile.style,
                "custom_prompt": dg.profile.custom_prompt,
                # Legacy aliases for backward compat
                "purpose": dg.profile.purpose or dg.profile.summary,
                "description": dg.profile.description,
            }
        else:
            item["profile"] = None
        result["digest_groups"].append(item)
    for oa in cfg.oa_groups:
        result["oa_groups"].append({
            "id": oa.id,
            "name": oa.name,
            "accounts": oa.accounts,
            "schedule": oa.schedule,
            "cron_expr": oa.cron_expr,
            "digest_template": oa.digest_template,
            "push_target": oa.push_target,
            "lookback_hours": oa.lookback_hours,
            "lookback_mode": oa.lookback_mode,
            "custom_prompt": oa.custom_prompt,
            "enabled": oa.enabled,
        })
    for st in cfg.scheduler_tasks:
        result["scheduler_tasks"].append({
            "id": st.id,
            "name": st.name,
            "task_type": st.task_type,
            "cron_expr": st.cron_expr,
            "ref_id": st.ref_id,
            "function_ref": st.function_ref,
            "enabled": st.enabled,
            "last_run_time": st.last_run_time,
            "status": st.status,
        })
    return result


def _queue_from_legacy(data: dict) -> NotificationQueue:
    queue_data = data.get("notification_queue") or {}
    enabled = queue_data.get("enabled")
    if enabled is None:
        legacy_channels = data.get("notify_channels", [])
        if legacy_channels:
            enabled = any(ch.get("enabled", True) for ch in legacy_channels)
        else:
            enabled = True
    retention = queue_data.get(
        "retention_hours",
        data.get("outbox_retention_hours", 24),
    )
    return NotificationQueue(enabled=bool(enabled), retention_hours=int(retention or 24))


def _migrate_oa_schedule_to_cron(schedule: list[str]) -> str:
    """Migrate legacy OAGroup.schedule list to a cron_expr string.

    Legacy formats found in the wild:
    - "09:00" (HH:MM) → "0 9 * * *"
    - "0 9" (partial cron) → "0 9 * * *"
    - "0 9 * * *" (full cron) → as-is
    - "20 * * *" (4-field, missing minute) → "0 20 * * *"

    Multiple entries are joined with \\n for multi-line cron support.
    """
    cron_lines = []
    for entry in schedule:
        entry = entry.strip()
        if not entry:
            continue
        # Full 5-field cron
        parts = entry.split()
        if len(parts) == 5:
            cron_lines.append(entry)
        elif len(parts) == 4:
            # Missing minute field — prepend "0"
            cron_lines.append(f"0 {entry}")
        elif len(parts) == 2:
            # Could be "0 9" (minute hour) or "09:00" (HH:MM)
            if ":" in entry:
                # HH:MM format
                try:
                    hh, mm = entry.split(":")
                    cron_lines.append(f"{int(mm)} {int(hh)} * * *")
                except ValueError:
                    logger.warning("Cannot migrate OA schedule entry %r, skipping", entry)
            else:
                # Assume "minute hour" partial cron
                cron_lines.append(f"{entry} * * *")
        elif len(parts) == 1 and ":" in entry:
            # Single "HH:MM" without spaces
            try:
                hh, mm = entry.split(":")
                cron_lines.append(f"{int(mm)} {int(hh)} * * *")
            except ValueError:
                logger.warning("Cannot migrate OA schedule entry %r, skipping", entry)
        else:
            logger.warning("Cannot migrate OA schedule entry %r, skipping", entry)

    return "\n".join(cron_lines)


def _dict_to_config(data: dict) -> AssistantConfig:
    """Deserialize dict to AssistantConfig."""
    # --- fav_export ---
    fe_data = data.get("fav_export") or {}
    fav_export = FavExportConfig(
        enabled=fe_data.get("enabled", False),
        output_dir=fe_data.get("output_dir", "data/fav_export"),
        formats=fe_data.get("formats", ["markdown", "json"]),
        last_export_timestamp=fe_data.get("last_export_timestamp", 0),
    )

    cfg = AssistantConfig(
        version=data.get("version", 1),
        assistant_enabled=data.get("assistant_enabled", False),
        allow_wechat_send=data.get("allow_wechat_send", False),
        notification_queue=_queue_from_legacy(data),
        fav_export=fav_export,
    )
    for ag_data in data.get("alert_groups", []):
        cfg.alert_groups.append(AlertGroup(
            chat_id=ag_data.get("chat_id", ""),
            group_name=ag_data.get("group_name", ""),
            keywords=ag_data.get("keywords", []),
            enabled=ag_data.get("enabled", True),
            push_target=ag_data.get("push_target", ""),
        ))
    for omg_data in data.get("oa_monitor_groups", []):
        cfg.oa_monitor_groups.append(OAMonitorGroup(
            id=omg_data.get("id", ""),
            name=omg_data.get("name", ""),
            accounts=omg_data.get("accounts", []),
            enabled=omg_data.get("enabled", True),
            push_target=omg_data.get("push_target", ""),
        ))
    for dg_data in data.get("digest_groups", []):
        profile = None
        p_data = dg_data.get("profile")
        if p_data:
            # Migrate legacy purpose/description into summary
            summary = p_data.get("summary", "")
            purpose = p_data.get("purpose", "")
            description = p_data.get("description", "")
            if not summary and (purpose or description):
                parts = [p for p in [purpose, description] if p]
                summary = "\n".join(parts)
            profile = GroupProfile(
                summary=summary,
                focus=p_data.get("focus", []),
                ignore=p_data.get("ignore", []),
                style=p_data.get("style", ""),
                custom_prompt=p_data.get("custom_prompt", ""),
                # Keep legacy fields for re-serialization compat
                purpose=purpose,
                description=description,
            )
        cfg.digest_groups.append(DigestGroup(
            chat_id=dg_data.get("chat_id", ""),
            group_name=dg_data.get("group_name", ""),
            schedule=dg_data.get("schedule", []),
            cron_expr=dg_data.get("cron_expr", ""),
            lookback_hours=dg_data.get("lookback_hours", 6),
            enabled=dg_data.get("enabled", True),
            profile=profile,
            memory=dg_data.get("memory", ""),
            unread_only=dg_data.get("unread_only", False),
            push_target=dg_data.get("push_target", ""),
        ))
    for oa_data in data.get("oa_groups", []):
        # Data migration: convert legacy schedule list to cron_expr
        cron_expr = oa_data.get("cron_expr", "")
        schedule = oa_data.get("schedule", [])
        if not cron_expr and schedule:
            cron_expr = _migrate_oa_schedule_to_cron(schedule)
        cfg.oa_groups.append(OAGroup(
            id=oa_data.get("id", ""),
            name=oa_data.get("name", ""),
            accounts=oa_data.get("accounts", []),
            schedule=[],  # Deprecated — use cron_expr
            cron_expr=cron_expr,
            digest_template=oa_data.get("digest_template", "default"),
            push_target=oa_data.get("push_target", ""),
            lookback_hours=oa_data.get("lookback_hours", 24),
            lookback_mode=oa_data.get("lookback_mode", "auto"),
            custom_prompt=oa_data.get("custom_prompt", ""),
            enabled=oa_data.get("enabled", True),
        ))
    for st_data in data.get("scheduler_tasks", []):
        # Handle legacy int last_run_time → convert to ISO-8601 string
        lrt = st_data.get("last_run_time", "")
        if isinstance(lrt, int) and lrt > 0:
            from datetime import datetime as _dt
            lrt = _dt.fromtimestamp(lrt).isoformat()
        elif isinstance(lrt, int):
            lrt = ""
        cfg.scheduler_tasks.append(SchedulerTask(
            id=st_data.get("id", ""),
            name=st_data.get("name", ""),
            task_type=st_data.get("task_type", ""),
            cron_expr=st_data.get("cron_expr", ""),
            ref_id=st_data.get("ref_id", ""),
            function_ref=st_data.get("function_ref", ""),
            enabled=st_data.get("enabled", True),
            last_run_time=lrt,
            status=st_data.get("status", "idle"),
        ))
    return cfg


def load_assistant_config() -> AssistantConfig:
    """Load assistant configuration from data/assistant_config.json.

    Creates a default config file if none exists.
    """
    if not CONFIG_PATH.exists():
        cfg = _default_config()
        save_assistant_config(cfg)
        logger.info("Created default assistant config at %s", CONFIG_PATH)
        return cfg

    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
        return _dict_to_config(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.warning("Failed to parse assistant config, using defaults: %s", e)
        cfg = _default_config()
        save_assistant_config(cfg)
        return cfg


def save_assistant_config(cfg: AssistantConfig) -> None:
    """Save assistant configuration to data/assistant_config.json."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    data = _config_to_dict(cfg)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
    logger.info("Assistant config saved to %s", CONFIG_PATH)
