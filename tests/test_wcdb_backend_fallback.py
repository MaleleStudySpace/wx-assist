from unittest.mock import Mock, patch

from src.wechat.wcdb_backend import WcdbBackend


def test_poll_group_reduces_limit_until_message_query_succeeds():
    client = Mock()
    client.get_messages.side_effect = [
        ValueError("WCDB query result too large or corrupted"),
        [{"local_id": 1, "create_time": 1}],
    ]
    backend = WcdbBackend(groups=["差评X.PIN"])
    backend._client = client
    backend._running = True
    backend._known_ids = Mock()
    backend._known_ids.__contains__ = Mock(return_value=True)

    backend._poll_group("差评X.PIN", "gh_2ba2404c01c0", Mock())

    assert [call.kwargs["limit"] for call in client.get_messages.call_args_list] == [50, 20]


def test_poll_group_uses_cached_limit_on_next_poll():
    client = Mock()
    client.get_messages.side_effect = [
        ValueError("WCDB query result too large or corrupted"),
        [{"local_id": 1, "create_time": 1}],
        [{"local_id": 2, "create_time": 2}],
    ]
    backend = WcdbBackend(groups=["差评X.PIN"])
    backend._client = client
    backend._running = True
    backend._known_ids = Mock()
    backend._known_ids.__contains__ = Mock(return_value=True)

    with patch("src.wechat.wcdb_backend.logger") as logger:
        backend._poll_group("差评X.PIN", "gh_2ba2404c01c0", Mock())
        backend._poll_group("差评X.PIN", "gh_2ba2404c01c0", Mock())

    assert [call.kwargs["limit"] for call in client.get_messages.call_args_list] == [50, 20, 20]
    assert logger.warning.call_count == 2
    logger.debug.assert_called_once()


def test_poll_group_moves_cached_limit_down_after_later_failure():
    client = Mock()
    client.get_messages.side_effect = [
        ValueError("too large"), [{"local_id": 1}],
        ValueError("too large"), [{"local_id": 2}],
    ]
    backend = WcdbBackend(groups=["差评X.PIN"])
    backend._client = client
    backend._running = True
    backend._known_ids = Mock()
    backend._known_ids.__contains__ = Mock(return_value=True)

    backend._poll_group("差评X.PIN", "gh_2ba2404c01c0", Mock())
    backend._poll_group("差评X.PIN", "gh_2ba2404c01c0", Mock())

    assert [call.kwargs["limit"] for call in client.get_messages.call_args_list] == [50, 20, 20, 10]


def test_poll_group_skips_cycle_after_all_message_query_limits_fail():
    client = Mock()
    client.get_messages.side_effect = ValueError("WCDB query result too large or corrupted")
    backend = WcdbBackend(groups=["差评X.PIN"])
    backend._client = client
    backend._running = True

    backend._poll_group("差评X.PIN", "gh_2ba2404c01c0", Mock())

    assert [call.kwargs["limit"] for call in client.get_messages.call_args_list] == [50, 20, 10, 5]
