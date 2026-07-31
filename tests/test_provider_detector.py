"""Tests for AI Provider detection."""

import importlib.util
import unittest
from unittest.mock import patch, MagicMock

# Load provider_detector directly (avoids anthropic import chain through __init__)
spec = importlib.util.spec_from_file_location(
    "provider_detector",
    "src/summarize/provider_detector.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

detect_provider = mod.detect_provider
ProviderInfo = mod.ProviderInfo
_try_openai_models = mod._try_openai_models
_try_openai_chat = mod._try_openai_chat
_try_anthropic_messages = mod._try_anthropic_messages


class TestProviderDetector(unittest.TestCase):

    def test_provider_info_defaults(self):
        info = ProviderInfo()
        self.assertEqual(info.provider_type, "")
        self.assertEqual(info.available_models, [])
        self.assertEqual(info.error, "")

    def test_detect_empty_input(self):
        info = detect_provider("", "")
        self.assertEqual(info.provider_type, "")
        self.assertTrue(info.error)

    @patch.object(mod, "requests")
    def test_models_endpoint_openai_format(self, mock_requests):
        """GET /models returns OpenAI format."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "object": "list",
            "data": [{"id": "gpt-4"}, {"id": "gpt-3.5-turbo"}],
        }
        mock_requests.get.return_value = mock_resp

        info = _try_openai_models("https://api.openai.com", "sk-test")
        self.assertIsNotNone(info)
        self.assertEqual(info.provider_type, "openai")
        self.assertEqual(info.available_models, ["gpt-4", "gpt-3.5-turbo"])

    @patch.object(mod, "requests")
    def test_models_endpoint_auth_error(self, mock_requests):
        """GET /models 401 — API key invalid → 返回带错误信息的 ProviderInfo。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_requests.get.return_value = mock_resp

        info = _try_openai_models("https://api.openai.com", "bad-key")
        self.assertIsNotNone(info)
        self.assertTrue(info.error)

    @patch.object(mod, "requests")
    def test_models_endpoint_failure(self, mock_requests):
        """GET /models returns 404."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_requests.get.return_value = mock_resp

        info = _try_openai_models("https://example.com", "sk-test")
        self.assertIsNone(info)

    @patch.object(mod, "requests")
    def test_openai_endpoint_200(self, mock_requests):
        """POST /v1/chat/completions returns 200."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_requests.post.return_value = mock_resp

        self.assertTrue(_try_openai_chat("https://api.openai.com", "sk-test"))

    @patch.object(mod, "requests")
    def test_openai_endpoint_400(self, mock_requests):
        """POST /v1/chat/completions returns 400 — still valid endpoint."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_requests.post.return_value = mock_resp

        self.assertTrue(_try_openai_chat("https://api.openai.com", "sk-test"))

    @patch.object(mod, "requests")
    def test_openai_endpoint_404(self, mock_requests):
        """POST /v1/chat/completions returns 404 — no such endpoint."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_requests.post.return_value = mock_resp

        self.assertFalse(_try_openai_chat("https://example.com", "sk-test"))

    @patch.object(mod, "requests")
    def test_openai_endpoint_auth_error(self, mock_requests):
        """POST /v1/chat/completions 401 — auth error → None。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_requests.post.return_value = mock_resp

        self.assertIsNone(_try_openai_chat("https://api.openai.com", "bad-key"))

    @patch.object(mod, "requests")
    def test_anthropic_endpoint_200(self, mock_requests):
        """POST /v1/messages returns 200."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_requests.post.return_value = mock_resp

        self.assertTrue(_try_anthropic_messages("https://api.anthropic.com", "sk-test"))

    @patch.object(mod, "requests")
    def test_anthropic_endpoint_400(self, mock_requests):
        """POST /v1/messages returns 400 — model invalid, but endpoint exists."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_requests.post.return_value = mock_resp

        self.assertTrue(_try_anthropic_messages("https://api.anthropic.com", "sk-test"))

    @patch.object(mod, "requests")
    def test_anthropic_endpoint_404(self, mock_requests):
        """POST /v1/messages returns 404."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_requests.post.return_value = mock_resp

        self.assertFalse(_try_anthropic_messages("https://example.com", "sk-test"))

    @patch.object(mod, "requests")
    def test_anthropic_endpoint_auth_error(self, mock_requests):
        """POST /v1/messages 401 — auth error → None。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_requests.post.return_value = mock_resp

        self.assertIsNone(_try_anthropic_messages("https://api.anthropic.com", "bad-key"))

    @patch.object(mod, "requests")
    def test_full_detect_all_fail(self, mock_requests):
        """All steps fail — return error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_requests.get.return_value = mock_resp
        mock_requests.post.return_value = mock_resp

        info = detect_provider("https://unknown.com", "sk-test")
        self.assertEqual(info.provider_type, "")
        self.assertTrue(info.error)


if __name__ == "__main__":
    unittest.main()
