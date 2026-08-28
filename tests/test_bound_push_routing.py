from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent.tools import ToolExecutor
from src.assistant.alert import AlertEngine
from src.assistant.config import AlertGroup, AssistantConfig
from src.assistant.scheduler import DigestScheduler
from src.scheduler.cron_scheduler import CronScheduler
from src.web import api_handlers


class _RecordingDelivery:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"success": True, "results": [{"success": True}]}

    def send_text(self, request):
        self.calls.append(request)
        return self.result


class _Outbox:
    def __init__(self):
        self.updated = []

    def add(self, **_kwargs):
        return 41

    def update_push_result(self, *args):
        self.updated.append(args)
        return True


def test_agent_cron_creation_keeps_auto_push_when_legacy_target_is_empty():
    jobs = []

    class FakeCron:
        def add_job(self, job):
            jobs.append(job)
            return "cron-1"

    executor = ToolExecutor.__new__(ToolExecutor)
    executor._cron_scheduler = FakeCron()
    executor._skill_engine = None

    result = executor._handle_create_cron(
        "自动任务", "sample", "0 8 * * *", push_target="",
    )

    assert jobs[0]["push"] == {"enabled": True, "target": "ilink"}
    assert "自动发送到消息推送页中已绑定的平台" in result


def test_cron_push_uses_bound_channel_for_record_and_ignores_legacy_target():
    outbox = _Outbox()
    delivery = _RecordingDelivery()
    scheduler = CronScheduler(outbox=outbox)
    job = {
        "name": "测试 Cron",
        "chat_id": "",
        "push": {"enabled": True, "target": ""},
    }

    with (
        patch("src.im.targets.bound_push_targets", return_value=["qqbot", "feishu"]),
        patch("src.im.delivery.get_delivery_service", return_value=delivery),
    ):
        scheduler._push(job, "执行结果")

    assert len(delivery.calls) == 1
    assert delivery.calls[0].platform == "qqbot"
    assert delivery.calls[0].auto_route is True
    assert outbox.updated[0][1] == "qqbot"


def test_manual_oa_digest_pushes_when_group_target_is_empty():
    outbox = _Outbox()
    delivery = _RecordingDelivery()
    group = SimpleNamespace(id="oa-1", name="科技组", push_target="")
    result = {"digest_text": "摘要内容", "articles_count": 1}

    with (
        patch("src.assistant.outbox.Outbox", return_value=outbox),
        patch("src.im.targets.bound_push_targets", return_value=["feishu"]),
        patch("src.im.delivery.get_delivery_service", return_value=delivery),
        patch("src.im.delivery.DeliveryService.format_text", side_effect=lambda _p, _t, text: text),
        patch.object(api_handlers, "get_task_center", return_value=None),
    ):
        api_handlers._push_oa_digest(result, group, AssistantConfig())

    assert len(delivery.calls) == 1
    assert delivery.calls[0].platform == "feishu"
    assert delivery.calls[0].auto_route is True
    assert outbox.updated[0][1] == "feishu"


def test_digest_failure_notice_pushes_when_oa_target_is_empty():
    outbox = _Outbox()
    delivery = _RecordingDelivery()
    scheduler = DigestScheduler(
        AssistantConfig(), outbox, summarizer=None, store=None,
    )
    oa = SimpleNamespace(id="oa-1", name="科技组", push_target="")

    with (
        patch("src.im.targets.bound_push_targets", return_value=["ilink"]),
        patch("src.im.delivery.get_delivery_service", return_value=delivery),
        patch("src.im.delivery.DeliveryService.format_text", side_effect=lambda _p, _t, text: text),
    ):
        scheduler._notify_oa_digest_failure(oa, "AI 暂时不可用", task_id=7)

    assert len(delivery.calls) == 1
    assert delivery.calls[0].platform == "ilink"
    assert delivery.calls[0].auto_route is True


def test_keyword_alert_pushes_when_legacy_target_is_empty():
    outbox = _Outbox()
    delivery = _RecordingDelivery()
    config = AssistantConfig(assistant_enabled=True)
    config.alert_groups = [AlertGroup(
        group_name="测试群", keywords=["关键词"], enabled=True, push_target="",
    )]
    engine = AlertEngine(config, outbox)

    with (
        patch("src.im.targets.bound_push_targets", return_value=["qqbot"]),
        patch("src.im.delivery.get_delivery_service", return_value=delivery),
        patch("src.im.delivery.DeliveryService.format_text", side_effect=lambda _p, _t, text: text),
    ):
        notification_id = engine.check({
            "chat_id": "group-1",
            "group_name": "测试群",
            "sender_name": "用户",
            "content": "包含关键词",
            "timestamp": 2_000_000_000,
        })

    assert notification_id == 41
    assert len(delivery.calls) == 1
    assert delivery.calls[0].platform == "qqbot"
    assert delivery.calls[0].auto_route is True
    assert outbox.updated[0][1] == "qqbot"
