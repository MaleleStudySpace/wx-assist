from pathlib import Path

from src.im.delivery import DeliveryRequest, DeliveryService, aggregate_status


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


# ── 三态推送状态：success / partial / failed / skipped ──────────────────
# 业务场景（关键词提醒、群聊摘要、公众号摘要、即时提醒、Cron）统一读
# result["status"]，不再各自用 all() 把部分失败塌缩成 failed。


def test_aggregate_status_covers_every_branch():
    assert aggregate_status([{"success": True}, {"success": True}]) == "success"
    assert aggregate_status([{"success": True}, {"success": False}]) == "partial"
    assert aggregate_status([{"success": False}, {"success": True}]) == "partial"
    assert aggregate_status([{"success": False}, {"success": False}]) == "failed"
    assert aggregate_status([{"success": True}]) == "success"
    assert aggregate_status([{"success": False}]) == "failed"
    # 没有任何渠道送达时按失败处理
    assert aggregate_status([]) == "failed"


def _send_two_platforms(monkeypatch, tmp_path, ilink_ok, qqbot_ok, source_id):
    channels = {
        "ilink": _AvailableChannel(ilink_ok),
        "qqbot": _AvailableChannel(qqbot_ok),
    }
    monkeypatch.setattr("src.im.delivery.get_plugin_push_channel", channels.get)
    service = DeliveryService(tmp_path / "delivery.db")
    return service.send_text(DeliveryRequest(
        platform='["wechat", "qqbot"]', text="hello", target="target",
        source_type="group_digest", source_id=source_id,
    ))


def test_send_text_reports_partial_when_one_channel_fails(monkeypatch, tmp_path):
    result = _send_two_platforms(monkeypatch, tmp_path, True, False, "partial-1")

    assert result["status"] == "partial"
    # success 保持 all() 语义不变 —— 日志与 WebSocket 广播的行为不受影响
    assert result["success"] is False
    assert "failed" in result["error"]


def test_send_text_reports_success_when_every_channel_delivers(monkeypatch, tmp_path):
    result = _send_two_platforms(monkeypatch, tmp_path, True, True, "success-1")

    assert result["status"] == "success"
    assert result["success"] is True
    assert result["error"] == ""


def test_send_text_reports_failed_when_no_channel_delivers(monkeypatch, tmp_path):
    result = _send_two_platforms(monkeypatch, tmp_path, False, False, "failed-1")

    assert result["status"] == "failed"
    assert result["success"] is False


def test_send_bound_text_reports_skipped_without_bound_channels(monkeypatch, tmp_path):
    monkeypatch.setattr("src.im.delivery.bound_push_targets", lambda: [])
    service = DeliveryService(tmp_path / "delivery.db")

    result = service.send_bound_text(DeliveryRequest(platform="wechat", text="hello"))

    assert result["status"] == "skipped"
    assert result["skipped"] is True


def test_send_text_reports_skipped_without_resolvable_platforms(monkeypatch, tmp_path):
    service = DeliveryService(tmp_path / "delivery.db")

    result = service.send_text(DeliveryRequest(platform="unknown-platform", text="hello"))

    assert result["status"] == "skipped"
    assert result["skipped"] is True


def _seed_attempt(service, source_type, seq):
    """Write one failed attempt with a deterministic created_at ordering."""
    service._record(
        DeliveryRequest(platform="feishu", text="t", source_type=source_type,
                        source_id=f"src-{source_type}-{seq}"),
        f"attempt-{source_type}-{seq}", "feishu", "", float(seq),
        {"success": False, "error": "boom"},
    )


def test_list_attempts_excludes_source_types_before_applying_limit(tmp_path):
    """排除必须在 SQL 层、LIMIT 之前生效。

    60 条交替写入（30 agent_reply + 30 keyword_alert），limit=50：
    若先取 50 条再过滤，只剩 25 条业务记录；SQL 层排除则能拿满 30 条。
    """
    service = DeliveryService(tmp_path / "delivery.db")
    for seq in range(60):
        source_type = "agent_reply" if seq % 2 == 0 else "keyword_alert"
        _seed_attempt(service, source_type, seq + 1)

    rows = service.list_attempts(limit=50, exclude_source_types=("agent_reply",))

    assert len(rows) == 30
    assert {row["source_type"] for row in rows} == {"keyword_alert"}


def test_list_recent_failures_still_reports_agent_reply_failures(tmp_path):
    """渠道健康探测不能排除 agent_reply —— 它的失败正是渠道不可用的信号。"""
    service = DeliveryService(tmp_path / "delivery.db")
    _seed_attempt(service, "agent_reply", 1)

    failures = service.list_recent_failures("feishu", limit=1)

    assert len(failures) == 1
    assert failures[0]["source_type"] == "agent_reply"


def test_list_attempts_filters_source_type_and_status_before_limit(tmp_path):
    """类型/状态筛选必须在 SQL 层、LIMIT 之前生效。

    60 条里只有最旧的 3 条是 cron。limit=50 时若先取最新 50 条再过滤，
    cron 一条都筛不出来（推送记录选"定时任务"显示空的成因）；SQL 层筛选
    则能拿到全部 3 条。
    """
    service = DeliveryService(tmp_path / "delivery.db")
    for seq in range(1, 4):
        _seed_attempt(service, "cron", seq)
    for seq in range(4, 61):
        _seed_attempt(service, "keyword_alert", seq)

    cron_rows = service.list_attempts(limit=50, source_type="cron")
    assert len(cron_rows) == 3
    assert {row["source_type"] for row in cron_rows} == {"cron"}

    # _seed_attempt 写入的都是 failed，用状态筛选同样要穿透 LIMIT 窗口
    failed_old = service.list_attempts(limit=50, source_type="cron", status="failed")
    assert len(failed_old) == 3
    assert service.list_attempts(limit=50, source_type="cron", status="success") == []
