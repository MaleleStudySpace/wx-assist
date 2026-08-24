from pathlib import Path

from src.im.delivery import DeliveryRequest, DeliveryService


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
