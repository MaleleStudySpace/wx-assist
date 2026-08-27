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
