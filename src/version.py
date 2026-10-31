"""应用版本与发布信息的唯一来源。

发版规则（重要）：
    本文件的 __version__ 必须与 GitHub Release 的 tag 严格对应
    （tag 带 v 前缀，例如 tag = v1.6.0  ->  __version__ = "1.6.0"）。

    发布前先跑 `python scripts/check_version.py` 校验，
    不一致会直接报错退出，避免又出现"程序里写着 1.0.1、Release 上却是 v1.6.0"的情况。

其它地方（PyInstaller spec、前端）一律从这里取值，不要再手写版本号。
"""

import re

__version__ = "1.9.0"

# ── 发布仓库（唯一正确地址，勿改成其它地址）────────────────────────
GITHUB_REPO = "MaleleStudySpace/wx-assist"

# ── Release 资产名（已冻结，改名会导致自动更新找不到文件）──────────
ASSET_FULL = "wx-assist.exe"         # 完整版（含 RAG）
ASSET_LITE = "wx-assist_no_rag.exe"  # 精简版（不含 RAG）

GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"


def version_tag() -> str:
    """GitHub Release 的 tag 形式，例如 v1.6.0。"""
    return f"v{__version__}"


def release_page_url(tag: str = "") -> str:
    """某个 tag 的 Release 页面地址；不传 tag 则为 Releases 列表页。"""
    if tag:
        return f"{GITHUB_RELEASES_URL}/tag/{tag.lstrip('vV')}"
    return GITHUB_RELEASES_URL


def parse_version(text: str) -> tuple:
    """把 'v1.6.0' / '1.6.0' / '1.6.0-rc1' 解析成可比较的数字元组。

    只取前导数字段，后缀（-rc1、+build 等）忽略；
    解析不出数字时返回空元组，调用方需自行兜底。
    """
    if not text:
        return ()
    match = re.match(r"[vV]?(\d+(?:\.\d+)*)", str(text).strip())
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(remote: str, local: str = "") -> bool:
    """远端版本是否比本地新（本地默认取 __version__）。"""
    remote_parts = parse_version(remote)
    if not remote_parts:
        return False
    local_parts = parse_version(local or __version__)
    length = max(len(remote_parts), len(local_parts))
    remote_padded = remote_parts + (0,) * (length - len(remote_parts))
    local_padded = local_parts + (0,) * (length - len(local_parts))
    return remote_padded > local_padded
