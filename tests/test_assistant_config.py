"""Tests for Assistant configuration."""

import json
import os
import unittest
import tempfile
from pathlib import Path

# Override CONFIG_PATH before importing
import src.assistant.config as config_mod


class TestAssistantConfig(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_path = config_mod.CONFIG_PATH
        config_mod.CONFIG_PATH = Path(self._tmpdir) / "assistant_config.json"

    def tearDown(self):
        config_mod.CONFIG_PATH = self._orig_path
        # Cleanup
        tmp = Path(self._tmpdir) / "assistant_config.json"
        if tmp.exists():
            tmp.unlink()
        Path(self._tmpdir).rmdir()

    def test_default_config(self):
        cfg = config_mod.load_assistant_config()
        self.assertFalse(cfg.assistant_enabled)
        self.assertEqual(cfg.version, 1)
        self.assertEqual(cfg.alert_groups, [])
        self.assertEqual(cfg.digest_groups, [])

    def test_save_and_load(self):
        cfg = config_mod.load_assistant_config()
        cfg.assistant_enabled = True
        cfg.alert_groups.append(config_mod.AlertGroup(
            group_name="测试群",
            keywords=["派单", "急"],
            enabled=True,
        ))
        config_mod.save_assistant_config(cfg)

        cfg2 = config_mod.load_assistant_config()
        self.assertTrue(cfg2.assistant_enabled)
        self.assertEqual(len(cfg2.alert_groups), 1)
        self.assertEqual(cfg2.alert_groups[0].group_name, "测试群")
        self.assertEqual(cfg2.alert_groups[0].keywords, ["派单", "急"])

    def test_digest_group_with_profile(self):
        cfg = config_mod.load_assistant_config()
        profile = config_mod.GroupProfile(
            purpose="抢单群",
            description="客户发需求",
            focus=["报价", "截止时间"],
            ignore=["闲聊"],
            style="偏行动项",
        )
        dg = config_mod.DigestGroup(
            group_name="抢单群A",
            schedule=["12:00", "18:00"],
            lookback_hours=6,
            enabled=True,
            profile=profile,
            memory="上次摘要要点...",
        )
        cfg.digest_groups.append(dg)
        config_mod.save_assistant_config(cfg)

        cfg2 = config_mod.load_assistant_config()
        self.assertEqual(len(cfg2.digest_groups), 1)
        dg2 = cfg2.digest_groups[0]
        self.assertEqual(dg2.group_name, "抢单群A")
        self.assertEqual(dg2.schedule, ["12:00", "18:00"])
        self.assertEqual(dg2.profile.purpose, "抢单群")
        self.assertEqual(dg2.profile.focus, ["报价", "截止时间"])
        self.assertEqual(dg2.memory, "上次摘要要点...")

    def test_config_to_dict_and_back(self):
        cfg = config_mod.load_assistant_config()
        cfg.assistant_enabled = True
        d = config_mod._config_to_dict(cfg)
        cfg2 = config_mod._dict_to_config(d)
        self.assertEqual(cfg2.assistant_enabled, cfg.assistant_enabled)

    def test_corrupted_config_recovery(self):
        """Corrupted JSON should fall back to defaults."""
        config_mod.CONFIG_PATH.write_text("not valid json {", encoding="utf-8")
        cfg = config_mod.load_assistant_config()
        self.assertFalse(cfg.assistant_enabled)  # back to default


if __name__ == "__main__":
    unittest.main()
