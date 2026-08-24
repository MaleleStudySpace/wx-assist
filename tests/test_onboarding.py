from src.im.plugins.qqbot.onboarding import QQOnboarding
from src.im.plugins.feishu.onboarding import FeishuOnboarding


def test_qq_onboarding_task_cancel_missing_is_safe():
    onboarding = QQOnboarding()
    assert onboarding.cancel("missing") is False


def test_feishu_onboarding_task_cancel_missing_is_safe():
    onboarding = FeishuOnboarding()
    assert onboarding.cancel("missing") is False
