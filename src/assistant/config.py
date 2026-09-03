"""Assistant configuration — load/save data/assistant_config.json."""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.utils.cron import validate_daily_cron

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("data/assistant_config.json")


@dataclass
class OAFullTextFetchConfig:
    """公众号全文缓存开关（只控制 content_cache 全文抓取线程）。

    enabled=False 时全文抓取线程停止抓取（新文章不缓存全文）；
    ignore_gh_ids 中的公众号不抓取全文。
    不影响 oa_monitor 推送/摘要逻辑。
    """
    enabled: bool = True
    ignore_gh_ids: list[str] = field(default_factory=list)


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
    custom_prompt: str = ""                             # 自定义 AI 总结 prompt
    dnd_start: str = ""                                 # 免打扰开始 "HH:MM"（空=不启用）
    dnd_end: str = ""                                   # 免打扰结束 "HH:MM"（空=不启用）


@dataclass
class GroupProfile:
    """群摘要档案 — 只保留输出风格相关配置。

    summary/focus/ignore 已移除：特殊需求直接写在 custom_prompt 中。
    """
    style: str = ""            # 摘要风格预设: "" | "行动项优先" | "完整复盘" | "极简速览" | "自定义"
    custom_prompt: str = ""    # 额外摘要指令


@dataclass
class DigestChat:
    """分组内的一个会话（群聊或好友）。"""
    chat_id: str = ""       # 必填，全配置内唯一 —— 一个会话只能属于一个分组
    name: str = ""          # 保存时的显示名快照，用于摘要分段标题与推送展示
    enabled: bool = True    # 会话级开关：分组开着但这个会话本轮不摘要


@dataclass
class DigestGroup:
    """定时群摘要的一个**分组**：一次调度、组内所有会话打包成一次摘要。

    与 OAGroup 同构（id / name / 成员列表 / cron / push_target）。
    旧的"一个会话一条配置"数据由 `_parse_digest_groups` 迁移成单会话分组。
    """
    id: str = ""                                          # 唯一 id: "dg_001"
    name: str = ""                                        # 分组显示名
    chats: list[DigestChat] = field(default_factory=list)  # 组内会话
    schedule: list[str] = field(default_factory=list)      # ["12:00", "18:00"]
    cron_expr: str = ""                                    # 高阶: cron 表达式 (5字段), 与 schedule 互斥
    lookback_hours: int = 6
    lookback_mode: str = "manual"                          # "auto" | "manual"（前端智能回溯）
    enabled: bool = True
    profile: Optional[GroupProfile] = None
    memory: str = ""            # 组级单一记忆（打包摘要天然只产出一份）
    memory_rev: int = 0         # 乐观并发版本号，后台写入时 +1
    memory_enabled: bool = True  # 群记忆开关: 关闭后摘要不再更新记忆
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
    rag_enabled: bool = False
    alert_groups: list[AlertGroup] = field(default_factory=list)
    oa_monitor_groups: list[OAMonitorGroup] = field(default_factory=list)
    digest_groups: list[DigestGroup] = field(default_factory=list)
    notification_queue: NotificationQueue = field(default_factory=NotificationQueue)
    oa_groups: list[OAGroup] = field(default_factory=list)
    fav_export: FavExportConfig = field(default_factory=FavExportConfig)
    scheduler_tasks: list[SchedulerTask] = field(default_factory=list)
    oa_full_text_fetch: OAFullTextFetchConfig = field(default_factory=OAFullTextFetchConfig)


def _default_config() -> AssistantConfig:
    """Return a sensible default configuration."""
    return AssistantConfig(
        version=1,
        assistant_enabled=False,
        rag_enabled=False,
        notification_queue=NotificationQueue(enabled=True, retention_hours=24),
    )


def _config_to_dict(cfg: AssistantConfig) -> dict:
    """Serialize AssistantConfig to JSON-safe dict."""
    # Import the digest system prompt for the frontend to reference
    from .digest import DIGEST_SYSTEM_PROMPT, STYLE_PRESETS

    result = {
        "version": cfg.version,
        "assistant_enabled": cfg.assistant_enabled,
        "rag_enabled": cfg.rag_enabled,
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
        "oa_full_text_fetch": {
            "enabled": cfg.oa_full_text_fetch.enabled,
            "ignore_gh_ids": list(cfg.oa_full_text_fetch.ignore_gh_ids),
        },
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
            "custom_prompt": omg.custom_prompt,
            "dnd_start": omg.dnd_start,
            "dnd_end": omg.dnd_end,
        })
    for dg in cfg.digest_groups:
        result["digest_groups"].append({
            "id": dg.id,
            "name": dg.name,
            "chats": [
                {"chat_id": c.chat_id, "name": c.name, "enabled": c.enabled}
                for c in dg.chats
            ],
            "schedule": dg.schedule,
            "cron_expr": dg.cron_expr,
            "lookback_hours": dg.lookback_hours,
            "lookback_mode": dg.lookback_mode,
            "enabled": dg.enabled,
            "memory": dg.memory,
            "memory_rev": dg.memory_rev,
            "memory_enabled": dg.memory_enabled,
            "unread_only": dg.unread_only,
            "push_target": dg.push_target,
            "profile": (
                {"style": dg.profile.style, "custom_prompt": dg.profile.custom_prompt}
                if dg.profile else None
            ),
        })
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


def _safe_int(value, default: int) -> int:
    """不抛异常的 int() —— 脏配置不能让 _parse_digest_groups 失败。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _next_digest_group_id(used: set) -> str:
    """生成 dg_001 式唯一 id（写法对齐 OAGroupManager.create_group）。"""
    n = len(used) + 1
    gid = f"dg_{n:03d}"
    while gid in used:
        n += 1
        gid = f"dg_{n:03d}"
    return gid


def _parse_digest_groups(raw_list) -> list:
    """解析 digest_groups，并把旧的"一会话一条"shape 迁移成单会话分组。

    纯函数、幂等、**不抛异常**：`load_assistant_config` 的 except 分支会用默认
    配置覆盖整份文件，所以这里任何一次抛错都可能永久抹掉不可再生的组记忆
    （线上 3 条配置累计 4408 字符 LLM 产物）。脏数据只 warning + 回落默认值。
    """
    if not isinstance(raw_list, list):
        return []

    groups = []
    used_ids: set = set()
    used_chats: dict = {}          # chat_id -> 组名，用于跨组去重

    for item in raw_list:
        if not isinstance(item, dict):
            continue

        gid = str(item.get("id") or "").strip()
        if not gid:
            gid = _next_digest_group_id(used_ids)
        used_ids.add(gid)

        gname = str(item.get("name") or item.get("group_name") or "").strip() or gid

        raw_chats = item.get("chats")
        if isinstance(raw_chats, list):
            chats = []
            for c in raw_chats:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("chat_id") or "").strip()
                if not cid:
                    continue
                if cid in used_chats:
                    logger.warning("会话 %s 同时出现在分组「%s」和「%s」，保留前者",
                                   cid, used_chats[cid], gname)
                    continue
                used_chats[cid] = gname
                chats.append(DigestChat(
                    chat_id=cid,
                    name=str(c.get("name") or ""),
                    enabled=bool(c.get("enabled", True)),
                ))
        else:
            # ── 旧 shape：chat_id / group_name → 单会话分组 ──
            legacy_cid = str(item.get("chat_id") or "").strip()
            if legacy_cid and legacy_cid not in used_chats:
                used_chats[legacy_cid] = gname
                chats = [DigestChat(chat_id=legacy_cid, name=gname, enabled=True)]
            elif legacy_cid:
                logger.warning("迁移：会话 %s 已被分组「%s」占用，「%s」的会话置空",
                               legacy_cid, used_chats[legacy_cid], gname)
                chats = []
            else:
                # agent 工具建的旧组只有 group_name、chat_id 恒空
                # （scheduler._resolve_chat_id 是死路，这类组从来没跑出过摘要）
                logger.warning("摘要分组「%s」没有可用会话（chat_id 为空），"
                               "请在网页端重新绑定", gname)
                chats = []

        p_data = item.get("profile")
        profile = None
        if isinstance(p_data, dict):
            # 旧数据可能带 summary/focus/ignore/purpose/description — 静默丢弃，
            # 只保留 style / custom_prompt。
            profile = GroupProfile(
                style=str(p_data.get("style") or ""),
                custom_prompt=str(p_data.get("custom_prompt") or ""),
            )

        groups.append(DigestGroup(
            id=gid,
            name=gname,
            chats=chats,
            schedule=list(item.get("schedule") or []),
            cron_expr=str(item.get("cron_expr") or ""),
            lookback_hours=_safe_int(item.get("lookback_hours"), 6),
            lookback_mode=str(item.get("lookback_mode") or "manual"),
            enabled=bool(item.get("enabled", True)),
            profile=profile,
            memory=str(item.get("memory") or ""),   # 逐字符原样带过去
            memory_rev=_safe_int(item.get("memory_rev"), 0),
            memory_enabled=bool(item.get("memory_enabled", True)),
            unread_only=bool(item.get("unread_only", False)),
            push_target=str(item.get("push_target") or ""),
        ))
    return groups


def merge_digest_groups(existing: list, incoming: list) -> list:
    """WebUI 批量 PUT 的合并规则：按 id upsert，memory / memory_rev 以磁盘为准。

    浏览器手里的 config 可能是几分钟前 GET 的，其间后台摘要已经写过记忆；
    不豁免就会把新记忆静默回滚成旧值。incoming 里没有的组视为已删除。
    """
    by_id = {g.id: g for g in existing if g.id}
    used_ids = set(by_id)
    out = []
    for inc in incoming:
        if inc.id and inc.id in by_id:
            disk = by_id[inc.id]
            inc.memory = disk.memory
            inc.memory_rev = disk.memory_rev
        else:
            if not inc.id:
                inc.id = _next_digest_group_id(used_ids)
            inc.memory = inc.memory or ""
            inc.memory_rev = 0
        used_ids.add(inc.id)
        out.append(inc)
    return out


def validate_digest_groups(raw_list) -> str:
    """校验前端提交的 digest_groups。返回 "" 表示合法，非空为可直接展示的错误。"""
    if not isinstance(raw_list, list):
        return "摘要分组格式不正确"
    seen: dict = {}
    for item in raw_list:
        if not isinstance(item, dict):
            return "摘要分组格式不正确"
        name = str(item.get("name") or item.get("group_name") or "").strip()
        if not name:
            return "摘要分组名称不能为空"
        raw_chats = item.get("chats")
        if isinstance(raw_chats, list):
            ids = [str(c.get("chat_id") or "").strip()
                   for c in raw_chats if isinstance(c, dict)]
            ids = [i for i in ids if i]
        elif str(item.get("chat_id") or "").strip():
            ids = [str(item["chat_id"]).strip()]
        else:
            ids = []
        if not ids:
            return f"分组「{name}」至少要选择一个会话"
        for cid in ids:
            if cid in seen:
                return (f"「{seen[cid]}」和「{name}」重复使用了同一个会话，"
                        f"一个会话只能属于一个分组")
            seen[cid] = name
    return ""


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
        rag_enabled=data.get("rag_enabled", False),
        notification_queue=_queue_from_legacy(data),
        fav_export=fav_export,
    )
    # --- oa_full_text_fetch（默认 enabled=True，兼容旧配置无此字段）---
    _ftf = data.get("oa_full_text_fetch") or {}
    cfg.oa_full_text_fetch = OAFullTextFetchConfig(
        enabled=_ftf.get("enabled", True),
        ignore_gh_ids=list(_ftf.get("ignore_gh_ids") or []),
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
            custom_prompt=omg_data.get("custom_prompt", ""),
            dnd_start=omg_data.get("dnd_start", ""),
            dnd_end=omg_data.get("dnd_end", ""),
        ))
    cfg.digest_groups = _parse_digest_groups(data.get("digest_groups") or [])
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


MEMORY_MAX_CHARS = 2000

# 配置是整份 JSON 全量覆盖写，而 WebUI HTTP 线程（max_workers=20）、
# scheduler 线程池（max_workers=3）、agent 工具（router 线程）都会写它，
# 各自持有不同的内存副本。锁只能防文件撕裂，防不了丢更新 —— 后者要靠
# mutate_config() 的"锁内重读磁盘再改"。RLock 因为 mutator 内允许再 load。
_CONFIG_LOCK = threading.RLock()


def _load_unlocked() -> AssistantConfig:
    if not CONFIG_PATH.exists():
        cfg = _default_config()
        _save_unlocked(cfg)
        logger.info("Created default assistant config at %s", CONFIG_PATH)
        return cfg

    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
        return _dict_to_config(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.warning("Failed to parse assistant config, using defaults: %s", e)
        # 落盘默认配置前先保住残骸：否则一次解析异常就会永久抹掉整份配置，
        # 包括不可再生的分组摘要记忆（LLM 逐次累积的产物）。
        try:
            CONFIG_PATH.replace(CONFIG_PATH.with_suffix(f".corrupt-{int(time.time())}"))
        except Exception as rename_err:
            logger.warning("Failed to keep corrupt config aside: %s", rename_err)
        cfg = _default_config()
        _save_unlocked(cfg)
        return cfg


def _save_unlocked(cfg: AssistantConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    data = _config_to_dict(cfg)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
    logger.info("Assistant config saved to %s", CONFIG_PATH)


def load_assistant_config() -> AssistantConfig:
    """Load assistant configuration from data/assistant_config.json.

    Creates a default config file if none exists.
    """
    with _CONFIG_LOCK:
        return _load_unlocked()


def save_assistant_config(cfg: AssistantConfig) -> None:
    """Save assistant configuration to data/assistant_config.json.

    整份覆盖写。如果调用方是先 load 再改再 save，请改用 `mutate_config()` ——
    那样写入基底是磁盘最新值，不会覆盖掉期间别人（尤其是后台摘要写记忆）的改动。
    """
    with _CONFIG_LOCK:
        _save_unlocked(cfg)


def mutate_config(mutator) -> AssistantConfig:
    """唯一合法的"改配置"入口：锁内重读磁盘 → 应用 mutator → 落盘。

    Returns:
        落盘的那份 AssistantConfig，可直接交给 scheduler.update_config()。
    """
    with _CONFIG_LOCK:
        cfg = _load_unlocked()
        mutator(cfg)
        _save_unlocked(cfg)
        return cfg


def update_digest_group_memory(group_id: str, memory: str) -> int:
    """后台摘要写组记忆的唯一入口。按 id 定位，不依赖调用方手里的对象引用。

    Returns:
        新的 memory_rev。

    Raises:
        KeyError: 分组不存在（可能刚被用户删掉）。
    """
    text = (memory or "")[:MEMORY_MAX_CHARS]
    new_rev = {}

    def _mutate(cfg: AssistantConfig) -> None:
        for g in cfg.digest_groups:
            if g.id == group_id:
                g.memory = text
                g.memory_rev += 1
                new_rev["rev"] = g.memory_rev
                return
        raise KeyError(group_id)

    mutate_config(_mutate)
    return new_rev["rev"]


def cas_digest_group_memory(group_id: str, memory: str,
                            expect_rev: int) -> tuple[bool, int, str]:
    """手工编辑记忆的乐观并发写入。

    Returns:
        (ok, 磁盘最新 memory_rev, 磁盘最新 memory)。
        ok=False 且 rev>=0 表示期间被后台摘要改过，调用方应把 memory 回填给用户；
        ok=False 且 rev==-1 表示分组不存在。
    """
    text = (memory or "")[:MEMORY_MAX_CHARS]
    with _CONFIG_LOCK:
        cfg = _load_unlocked()
        for g in cfg.digest_groups:
            if g.id == group_id:
                if g.memory_rev != expect_rev:
                    logger.warning("记忆 CAS 冲突：group=%s expect_rev=%s disk_rev=%s",
                                   group_id, expect_rev, g.memory_rev)
                    return False, g.memory_rev, g.memory
                g.memory = text
                g.memory_rev += 1
                _save_unlocked(cfg)
                return True, g.memory_rev, g.memory
        return False, -1, ""
