from unittest.mock import MagicMock

import pytest

import src.wechat.ilink_receiver as receiver_module
from src.wechat.ilink_receiver import ILinkReceiver, POLL_TIMEOUT_SEC


def test_stop_receiver_without_instance_succeeds(monkeypatch):
    monkeypatch.setattr(receiver_module, "_receiver_instance", None)

    assert receiver_module.stop_receiver() is True


def test_receiver_stop_waits_for_long_poll_and_clears_thread():
    receiver = ILinkReceiver()
    thread = MagicMock()
    thread.is_alive.return_value = False
    receiver._thread = thread
    receiver._account = {"account_id": "old"}
    receiver._running = True

    assert receiver.stop() is True
    thread.join.assert_called_once_with(timeout=2)
    assert receiver._thread is None
    assert receiver._account is None
    assert receiver._running is False


def test_stop_receiver_keeps_instance_when_thread_did_not_exit(monkeypatch):
    receiver = ILinkReceiver()
    thread = MagicMock()
    thread.is_alive.return_value = True
    receiver._thread = thread
    receiver._running = True
    monkeypatch.setattr(receiver_module, "_receiver_instance", receiver)

    assert receiver_module.stop_receiver() is True
    assert receiver_module._receiver_instance is None
    thread.join.assert_called_once_with(timeout=2)


def test_start_receiver_does_not_replace_running_instance(monkeypatch):
    receiver = ILinkReceiver()
    receiver._running = True
    monkeypatch.setattr(receiver_module, "_receiver_instance", receiver)

    assert receiver_module.start_receiver({"account_id": "new"}, lambda _: None) is False
    assert receiver_module._receiver_instance is receiver


def test_callback_failure_does_not_mark_message_as_processed(monkeypatch):
    receiver = ILinkReceiver()
    receiver._running = True
    receiver._callback = MagicMock(side_effect=RuntimeError("temporary failure"))
    message = {"msg_id": "message-1", "from_user_id": "user-1", "text": "hello"}

    with pytest.raises(RuntimeError):
        receiver._handle_message(message)

    assert "message-1" not in receiver._recent_msg_ids


def test_callback_success_marks_message_after_processing(monkeypatch):
    receiver = ILinkReceiver()
    receiver._running = True
    receiver._callback = MagicMock(return_value=None)
    message = {"msg_id": "message-2", "from_user_id": "user-1", "text": "hello"}

    receiver._handle_message(message)

    assert "message-2" in receiver._recent_msg_ids


def test_poll_persists_cursor_only_after_all_callbacks_succeed(monkeypatch):
    receiver = ILinkReceiver()
    receiver._running = True
    receiver._sync_buf = "old"
    receiver._account = {"bot_token": "token"}
    receiver._session = MagicMock()
    receiver._sleep = MagicMock(side_effect=lambda _seconds: setattr(receiver, "_running", False))
    callback = MagicMock(side_effect=RuntimeError("temporary failure"))
    receiver._callback = callback
    monkeypatch.setattr(receiver_module, "fetch_updates", lambda *_args: {
        "messages": [{"msg_id": "message-3", "from_user_id": "user-1", "text": "hello"}],
        "new_sync_buf": "new",
        "session_expired": False,
    })
    save_buf = MagicMock()
    monkeypatch.setattr(receiver_module, "_save_sync_buf", save_buf)

    receiver._poll_loop()

    assert receiver._sync_buf == "old"
    save_buf.assert_not_called()


def test_poll_persists_cursor_after_all_callbacks_succeed(monkeypatch):
    receiver = ILinkReceiver()
    receiver._running = True
    receiver._sync_buf = "old"
    receiver._account = {"bot_token": "token"}
    receiver._session = MagicMock()
    receiver._sleep = MagicMock(side_effect=lambda _seconds: setattr(receiver, "_running", False))
    receiver._callback = MagicMock(return_value=None)
    monkeypatch.setattr(receiver_module, "fetch_updates", lambda *_args: {
        "messages": [{"msg_id": "message-4", "from_user_id": "user-1", "text": "hello"}],
        "new_sync_buf": "new",
        "session_expired": False,
    })
    save_buf = MagicMock()
    monkeypatch.setattr(receiver_module, "_save_sync_buf", save_buf)

    receiver._poll_loop()

    assert receiver._sync_buf == "new"
    save_buf.assert_called_once_with("new")
