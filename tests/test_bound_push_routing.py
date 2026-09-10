from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent.tools import ToolExecutor
from src.assistant.alert import AlertEngine
from src.assistant.config import AlertGroup, AssistantConfig, DigestGroup, OAGroup
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


class _FailingDelivery(_RecordingDelivery):
    def __init__(self):
        super().__init__({"success": False, "error": "provider failed", "results": []})


def test_agent_digest_lookup_tools_return_complete_json_payloads():
    import json

    task_center = MagicMock()
    task_center.get_latest_completed_digest.side_effect = [
        {
            "id": 101,
            "task_type": "oa_digest",
            "source": "scheduler",
            "group_id": "oa-1",
            "group_name": "开源项目",
            "created_at": "2026-09-07T08:00:00",
            "started_at": "2026-09-07T08:00:01",
            "finished_at": "2026-09-07T08:01:00",
            "articles_count": 2,
            "msg_count": 0,
            "result": "公众号完整摘要正文" * 100,
        },
        {
            "id": 102,
            "task_type": "group_digest",
            "source": "catchup",
            "group_id": "dg-1",
            "group_name": "技术交流群",
            "created_at": "2026-09-07T08:00:00",
            "started_at": "2026-09-07T08:00:01",
            "finished_at": "2026-09-07T08:01:00",
            "articles_count": 0,
            "msg_count": 8,
            "result": "群聊完整摘要正文",
        },
    ]
    executor = ToolExecutor.__new__(ToolExecutor)
    executor._task_center = task_center

    oa = json.loads(executor._handle_get_latest_oa_digest("开源项目"))
    group = json.loads(executor._handle_get_latest_group_digest("技术交流群"))

    assert oa["ok"] is True
    assert oa["has_content"] is True
    assert oa["task_id"] == 101
    assert len(oa["digest"]) == len("公众号完整摘要正文" * 100)
    assert group["task_id"] == 102
    assert group["msg_count"] == 8
    assert task_center.get_latest_completed_digest.call_args_list[0].args == (
        "oa_digest", "开源项目"
    )
    assert task_center.get_latest_completed_digest.call_args_list[1].args == (
        "group_digest", "技术交流群"
    )


def test_agent_digest_lookup_returns_no_content_payload():
    import json

    executor = ToolExecutor.__new__(ToolExecutor)
    executor._task_center = MagicMock()
    executor._task_center.get_latest_completed_digest.return_value = {
        "id": 103,
        "task_type": "oa_digest",
        "source": "scheduler",
        "group_id": "oa-1",
        "group_name": "开源项目",
        "created_at": "2026-09-07T08:00:00",
        "finished_at": "2026-09-07T08:00:10",
        "articles_count": 0,
        "msg_count": 0,
        "result": "所有文章已摘要过，无新内容",
    }

    result = json.loads(executor._handle_get_latest_oa_digest("开源项目"))

    assert result["ok"] is True
    assert result["has_content"] is False
    assert result["task_id"] == 103
    assert result["reason"] == "所有文章已摘要过，无新内容"


def test_agent_digest_lookup_requires_task_center_and_exact_name():
    executor = ToolExecutor.__new__(ToolExecutor)
    executor._task_center = None
    assert "任务中心未就绪" in executor._handle_get_latest_oa_digest("开源项目")

    executor._task_center = MagicMock()
    assert "请提供分组名称" in executor._handle_get_latest_group_digest("  ")
    executor._task_center.get_latest_completed_digest.return_value = None
    assert "未找到" in executor._handle_get_latest_oa_digest("开源项目", 24)


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

    assert jobs[0]["push"] == {"enabled": True, "target": ""}
    assert "自动发送到消息推送页中已绑定的平台" in result


def test_agent_cron_creation_supports_explicit_silent_mode():
    jobs = []

    class FakeCron:
        def add_job(self, job):
            jobs.append(job)
            return "cron-silent"

    executor = ToolExecutor.__new__(ToolExecutor)
    executor._cron_scheduler = FakeCron()
    executor._skill_engine = None

    result = executor._handle_create_cron(
        "静默任务", "sample", "0 9 * * *", push_enabled=False,
    )

    assert jobs[0]["push"] == {"enabled": False, "target": ""}
    assert "静默任务，不发送推送" in result


def test_agent_cron_schema_exposes_push_enabled_boolean():
    executor = ToolExecutor.__new__(ToolExecutor)
    executor._cron_scheduler = object()
    executor.registry = MagicMock()
    executor._register_cron_tools()

    schema = executor.registry.register.call_args_list[0].kwargs["parameters"]
    assert schema["properties"]["push_enabled"] == {
        "type": "boolean",
        "description": "是否发送推送；false 表示静默任务，默认 true",
        "default": True,
    }


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
    # Two bound channels fan out and Outbox keeps a single channel field,
    # so the legacy column must stay empty rather than naming only the
    # first bound platform.
    assert outbox.updated[0][1] == ""


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
    task_center = MagicMock()
    oa = SimpleNamespace(id="oa-1", name="科技组", push_target="")

    with (
        patch("src.im.targets.bound_push_targets", return_value=["ilink"]),
        patch("src.im.delivery.get_delivery_service", return_value=delivery),
        patch("src.im.delivery.DeliveryService.format_text", side_effect=lambda _p, _t, text: text),
    ):
        scheduler._task_center = task_center
        scheduler._notify_oa_digest_failure(oa, "AI 暂时不可用", task_id=7)

    assert len(delivery.calls) == 1
    request = delivery.calls[0]
    assert request.platform == "ilink"
    assert request.auto_route is True
    assert request.notification_id == "41"
    assert request.outbox_id == 41
    assert request.task_id == 7
    assert outbox.updated[-1] == (41, "ilink", "success", "")


def test_digest_failure_notice_marks_failed_delivery_in_outbox_and_task():
    outbox = _Outbox()
    delivery = _FailingDelivery()
    scheduler = DigestScheduler(
        AssistantConfig(), outbox, summarizer=None, store=None,
    )
    task_center = MagicMock()
    oa = SimpleNamespace(id="oa-2", name="科技组", push_target="")

    with (
        patch("src.im.targets.bound_push_targets", return_value=["qqbot"]),
        patch("src.im.delivery.get_delivery_service", return_value=delivery),
        patch("src.im.delivery.DeliveryService.format_text", side_effect=lambda _p, _t, text: text),
    ):
        scheduler._task_center = task_center
        scheduler._notify_oa_digest_failure(oa, "AI 暂时不可用", task_id=8)

    request = delivery.calls[0]
    assert request.notification_id == "41"
    assert request.outbox_id == 41
    assert request.task_id == 8
    assert outbox.updated[-1] == (41, "qqbot", "failed", "provider failed")
    task_center.update_push_result.assert_called_once_with(8, "failed", "provider failed")


def test_digest_failure_notice_records_skipped_when_no_bound_channel():
    outbox = _Outbox()
    delivery = _RecordingDelivery()
    scheduler = DigestScheduler(
        AssistantConfig(), outbox, summarizer=None, store=None,
    )
    task_center = MagicMock()
    oa = SimpleNamespace(id="oa-3", name="科技组", push_target="")

    with patch("src.im.targets.bound_push_targets", return_value=[]):
        scheduler._task_center = task_center
        scheduler._notify_oa_digest_failure(oa, "AI 暂时不可用", task_id=9)

    assert delivery.calls == []
    assert outbox.updated[-1] == (41, "", "skipped", "未绑定任何推送渠道")
    task_center.update_push_result.assert_called_once_with(9, "skipped", "未绑定任何推送渠道")


def test_scheduled_overview_push_label_ignores_legacy_target():
    config = AssistantConfig()
    config.digest_groups = [
        DigestGroup(id="dg_001", name="自动群", push_target="", enabled=True),
        DigestGroup(id="dg_002", name="停用群", push_target="ilink", enabled=False),
    ]
    config.oa_groups = [
        OAGroup(name="自动公众号", push_target="qqbot", enabled=True),
        OAGroup(name="停用公众号", push_target="", enabled=False),
    ]

    result = api_handlers.handle_scheduled_tasks_overview({}, config)
    labels = {item["name"]: item["push"] for item in result["data"]["tasks"]}

    assert labels == {
        "自动群": "推送",
        "停用群": "未启用",
        "自动公众号": "推送",
        "停用公众号": "未启用",
    }


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
