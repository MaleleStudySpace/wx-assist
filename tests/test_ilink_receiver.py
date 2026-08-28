from unittest.mock import MagicMock

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
