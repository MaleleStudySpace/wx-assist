"""WCDB JSON 读取错误分类与启动降级的回归测试。

覆盖：
- ``_read_gbk_string_ex`` 能识别输出被截断（结果过大）。
- ``_read_gbk_string`` 保持原有签名/行为（非 JSON 调用方兼容）。
- ``_call_json_inner`` 区分「结果过大」与「数据损坏」，意外异常仍返回 {}。
- 群组解析失败时 ``WcdbBackend.start`` 不再让整个服务崩溃。
"""
import ctypes as ct
from unittest.mock import Mock

import pytest

from src.wechat.wcdb_backend import WcdbBackend
from src.wechat.wcdb_client import (
    WcdbNativeClient,
    _read_gbk_string,
    _read_gbk_string_ex,
)


def _ct_string(data: bytes):
    """返回 (c_void_p 指针, 保活 buffer)，防止 buffer 被回收。"""
    buf = ct.create_string_buffer(data)
    return ct.cast(buf, ct.c_void_p), buf


# ── 读取层：截断检测 ──────────────────────────────────────────────

def test_read_gbk_string_ex_marks_complete_payload():
    ptr, _keep = _ct_string(b'{"ok": true}')
    text, truncated = _read_gbk_string_ex(ptr)
    assert text == '{"ok": true}'
    assert truncated is False


def test_read_gbk_string_ex_detects_truncation():
    ptr, _keep = _ct_string(b'{"ok": true}')
    _text, truncated = _read_gbk_string_ex(ptr, max_bytes=4)
    assert truncated is True


def test_read_gbk_string_wrapper_signature_unchanged():
    """非 JSON 调用方仍拿到纯字符串（保持向后兼容）。"""
    ptr, _keep = _ct_string(b'{"ok": true}')
    assert _read_gbk_string(ptr) == '{"ok": true}'


# ── JSON 解析层：异常分类 ─────────────────────────────────────────

def _fake_client():
    """绕过 __init__，避免触发真实 DLL 加载。"""
    client = WcdbNativeClient.__new__(WcdbNativeClient)
    client._dll = Mock()
    return client


def _dll_func(ptr_value=1):
    """模拟返回 JSON 指针的 DLL 函数：把 out 指针写成非 0 后返回 0。"""
    def func(_handle, out_ref):
        out_ref._obj.value = ptr_value
        return 0
    return func


def test_call_json_inner_raises_too_large_when_truncated(monkeypatch):
    monkeypatch.setattr(
        "src.wechat.wcdb_client._read_gbk_string_ex",
        lambda _ptr: ('{"a": 1', True),
    )
    client = _fake_client()
    with pytest.raises(ValueError) as exc:
        client._call_json_inner(_dll_func(), 0)
    assert "too large" in str(exc.value)


def test_call_json_inner_raises_corrupted_without_truncation(monkeypatch):
    monkeypatch.setattr(
        "src.wechat.wcdb_client._read_gbk_string_ex",
        lambda _ptr: ("not-json", False),
    )
    client = _fake_client()
    with pytest.raises(ValueError) as exc:
        client._call_json_inner(_dll_func(), 0)
    message = str(exc.value)
    assert "corrupted" in message
    assert "too large" not in message


def test_call_json_inner_keeps_swallowing_unexpected_errors(monkeypatch):
    """非解析类异常必须仍返回 {}（保持原有容错行为）。"""
    def boom(_ptr):
        raise RuntimeError("unexpected")

    monkeypatch.setattr("src.wechat.wcdb_client._read_gbk_string_ex", boom)
    client = _fake_client()
    assert client._call_json_inner(_dll_func(), 0) == {}


# ── 启动层：群组解析失败不再让服务崩溃 ────────────────────────────

def test_start_survives_group_resolution_failure(monkeypatch):
    pushed: list[str] = []
    monkeypatch.setattr(
        WcdbBackend,
        "_push_start_error",
        staticmethod(lambda message: pushed.append(message)),
    )
    client = Mock()
    client._config = {}
    client.get_sessions.side_effect = ValueError(
        "WCDB query result too large (truncated at 500000 bytes)"
    )
    monkeypatch.setattr(
        "src.wechat.wcdb_backend.WcdbNativeClient", lambda *a, **k: client
    )

    backend = WcdbBackend(groups=["测试群"])
    backend.start(Mock())  # 不应抛出

    assert backend._talker_ids == {}
    assert pushed, "启动失败原因应推送到运行状态页"
