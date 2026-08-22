from src.im.plugins.wechat.push import WechatPushChannel


def test_wechat_channel_preserves_legacy_contract():
    class FakeILink:
        def __init__(self):
            self.calls = []

        def is_available(self):
            return True

        def is_healthy(self):
            return True

        def get_status(self):
            return {"bound": True, "push_ok": True}

        def send_message(self, text, progress_callback=None):
            self.calls.append((text, progress_callback))
            return {"success": True, "error": ""}

    impl = FakeILink()
    channel = WechatPushChannel(implementation=impl)
    assert channel.channel_name == "ilink"
    assert channel.platform == "wechat"
    assert channel.is_available() is True
    assert channel.is_healthy() is True
    assert channel.get_status()["bound"] is True
    assert channel.format_message("标题", "正文") == "标题\n\n正文"
    assert channel.send_message("正文", target="qqbot:ignored")["success"] is True
    assert impl.calls == [("正文", None)]
