from src.im.config_schema import PlatformConfig
from src.im.registry import PlatformRegistry


def test_qq_missing_credentials_fails_only_qq():
    created = []

    class FakeAdapter:
        def __init__(self, config):
            created.append(config.name)

        def start(self, callback, groups=None):
            if self.name == "qqbot":
                return False
            return True

        def stop(self):
            pass

        def health_status(self):
            return {"ok": True}

        def list_chats(self):
            return []

    def factory(config):
        if config.name == "qqbot":
            raise RuntimeError("QQ credentials missing")
        raise AssertionError("wechat should not be started by this isolated fixture")

    registry = PlatformRegistry(factory=factory)
    result = registry.start_all(
        [PlatformConfig("qqbot", enabled=True)],
        lambda message: None,
    )
    assert result["started"] == []
    assert "qqbot" in result["failed"]
    assert registry.get_health()["qqbot"]["ok"] is False
