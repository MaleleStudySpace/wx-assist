from pathlib import Path

from src.im.delivery import DeliveryRequest, DeliveryService


class _AvailableChannel:
    def __init__(self, success=True):
        self.success = success

    def is_available(self):
        return True

    def send_message(self, text, *, target=None):
        return {"success": self.success, "error": "failed" if not self.success else ""}


def test_delivery_records_each_normalized_channel_for_multi_platform(monkeypatch, tmp_path):
    channels = {
        "ilink": _AvailableChannel(),
        "qqbot": _AvailableChannel(False),
    }
    monkeypatch.setattr("src.im.delivery.get_plugin_push_channel", channels.get)
    service = DeliveryService(tmp_path / "delivery.db")

    result = service.send_text(DeliveryRequest(
        platform='["wechat", "qqbot"]', text="hello", target="target",
        source_type="group_digest", source_id="42", outbox_id=7, task_id=8,
    ))

    assert result["success"] is False
    rows = service.list_attempts(source_type="group_digest", source_id="42")
    assert {(row["platform"], row["channel"], row["outbox_id"], row["task_id"]) for row in rows} == {
        ("ilink", "ilink", 7, 8),
        ("qqbot", "qqbot", 7, 8),
    }


def test_delivery_records_canonical_ilink_for_wechat_alias(monkeypatch, tmp_path):
    # Ensure deterministic: no auto-route, channel unavailable -> failure
    monkeypatch.setattr("src.im.delivery.bound_push_targets", lambda: [])
    monkeypatch.setattr("src.im.delivery.get_plugin_push_channel", lambda _: None)
    service = DeliveryService(tmp_path / "delivery.db")
    result = service.send_text(DeliveryRequest(
        platform="wechat", text="hello", target="wechat-user",
        source_type="group_digest", source_id="42",
    ))
    assert result["success"] is False
    rows = service.list_attempts(source_type="group_digest", source_id="42")
    assert len(rows) == 1
    assert rows[0]["platform"] == "ilink"
    assert rows[0]["channel"] == "ilink"
    assert rows[0]["outbox_id"] == 0


def test_delivery_service_records_unavailable_platform(tmp_path):
    service = DeliveryService(tmp_path / "delivery.db")
    result = service.send_text(DeliveryRequest(
        platform="qqbot", text="hello", target="qqbot:user",
        source_type="agent_reply", conversation_key="qqbot:user",
    ))
    assert result["success"] is False
    rows = service.list_attempts("qqbot")
    assert len(rows) == 1
    assert rows[0]["source_type"] == "agent_reply"
    assert rows[0]["target"] == "qqbot:user"
