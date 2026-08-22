from src.im.config_schema import PlatformConfig
from src.im.plugins.feishu.adapter import FeishuAdapter


def test_feishu_missing_credentials_does_not_start():
    adapter = FeishuAdapter(PlatformConfig("feishu"))
    assert adapter.start(lambda message: None) is False


def test_feishu_invalid_verification_token_rejected():
    adapter = FeishuAdapter(PlatformConfig(
        "feishu", extra={"app_id": "a", "app_secret": "s", "verification_token": "good"}
    ))
    try:
        adapter.handle_webhook({
            "type": "url_verification", "token": "bad", "challenge": "x"
        })
    except ValueError as exc:
        assert "mismatch" in str(exc)
    else:
        raise AssertionError("invalid verification token was accepted")
