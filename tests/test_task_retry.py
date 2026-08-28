from unittest.mock import MagicMock, patch

import src.web.api_handlers as handlers


def test_retry_targets_only_failed_channels():
    class FakeDelivery:
        def __init__(self):
            self.calls = []

        def list_attempts(self, **kwargs):
            return [
                {"platform": "ilink", "channel": "ilink", "target": "", "status": "success"},
                {"platform": "qqbot", "channel": "qqbot", "target": "qqbot:user", "status": "failed"},
            ]

        def send_text(self, request):
            self.calls.append(request)
            return {"success": True, "results": [{"success": True}]}

    delivery = FakeDelivery()
    outbox = MagicMock()
    outbox.get_notification.return_value = {"title": "title", "content": "content"}
    with (
        patch("src.im.delivery.get_delivery_service", return_value=delivery),
        patch("src.im.delivery.DeliveryService.format_text", side_effect=lambda platform, title, text: text),
        patch("src.assistant.outbox.Outbox", return_value=outbox),
    ):
        task = {"id": 1, "task_type": "group_digest", "outbox_id": 9, "group_id": "group"}
        result = handlers._do_task_retry_push(task)

    assert result["success"] is True
    assert len(delivery.calls) == 1
    assert delivery.calls[0].platform == "qqbot"
    assert delivery.calls[0].target == "qqbot:user"


def test_retry_uses_latest_attempt_instead_of_any_historical_success():
    class FakeDelivery:
        def __init__(self):
            self.calls = []

        def list_attempts(self, **kwargs):
            return [
                # Newest first: the latest iLink attempt failed.
                {"platform": "ilink", "channel": "ilink", "target": "", "status": "failed", "created_at": 200},
                {"platform": "ilink", "channel": "ilink", "target": "", "status": "success", "created_at": 100},
                {"platform": "qqbot", "channel": "qqbot", "target": "qqbot:user", "status": "success", "created_at": 150},
            ]

        def send_text(self, request):
            self.calls.append(request)
            return {"success": True, "results": [{"success": True}]}

    delivery = FakeDelivery()
    outbox = MagicMock()
    outbox.get_notification.return_value = {"title": "title", "content": "content"}
    with (
        patch("src.im.delivery.get_delivery_service", return_value=delivery),
        patch("src.im.delivery.DeliveryService.format_text", side_effect=lambda platform, title, text: text),
        patch("src.assistant.outbox.Outbox", return_value=outbox),
    ):
        task = {"id": 2, "task_type": "group_digest", "outbox_id": 10, "group_id": "group"}
        result = handlers._do_task_retry_push(task)

    assert result["success"] is True
    assert len(delivery.calls) == 1
    assert delivery.calls[0].platform == "ilink"
    assert delivery.calls[0].target == ""
