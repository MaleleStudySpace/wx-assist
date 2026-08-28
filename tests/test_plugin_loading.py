from src.im.plugins import _ADAPTERS, get_plugin_push_channel, load_builtin_plugins
from src.im.plugins.wechat import WechatAdapter


def test_builtin_plugin_loading_excludes_placeholder_wechat_adapter():
    load_builtin_plugins()

    assert "wechat" not in _ADAPTERS
    assert "qqbot" in _ADAPTERS
    assert "feishu" in _ADAPTERS
    assert WechatAdapter.platform_name == "wechat"


def test_ilink_channel_still_loads_real_wechat_push_channel():
    channel = get_plugin_push_channel("ilink")

    assert channel is not None
    assert channel.channel_name == "ilink"
    assert channel.platform == "wechat"
