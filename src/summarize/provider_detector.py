"""AI Provider auto-detection.

Accepts provider_type hint ("openai" | "anthropic" | "custom"):
  - "openai":    probe {base_url}/models, fallback to chat/completions
  - "anthropic": skip /models, probe /v1/messages directly
  - "custom":   no probing at all
  - "auto":      try both (legacy behavior)

URL normalization for OpenAI format:
  - bare domain → append /v1
  - has path   → use as-is
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10.0


@dataclass
class ProviderInfo:
    provider_type: str = ""       # "anthropic" | "openai" | ""
    available_models: list[str] = field(default_factory=list)
    error: str = ""


def _normalize_base_url(base_url: str, provider_type: str = "openai") -> str:
    """Normalize base_url for the given provider type.

    OpenAI SDK: base_url should include path prefix (e.g. /v1).
      - bare domain → append /v1
      - has path   → keep as-is

    Anthropic SDK: base_url is the root, SDK appends /v1/messages.
      - always keep as-is

    Custom: always keep as-is.
    """
    url = base_url.rstrip("/")
    if provider_type == "openai":
        # Bare domain (no path after domain) → append /v1
        path_part = url.split("://", 1)[-1]
        if not re.search(r'/\w', path_part):
            url += "/v1"
    return url


def _try_openai_models(base_url: str, api_key: str) -> Optional[ProviderInfo]:
    """GET {base_url}/models for OpenAI-compatible providers."""
    url = _normalize_base_url(base_url, "openai") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code in (401, 403):
            logger.debug("GET models returned %d (auth error)", resp.status_code)
            return ProviderInfo(error="API Key 无效或站点地址错误，请检查后重试。")
        if resp.status_code != 200:
            logger.debug("GET models returned %d", resp.status_code)
            return None
        data = resp.json()

        if isinstance(data, dict) and "data" in data:
            models = [m["id"] for m in data.get("data", [])
                      if isinstance(m, dict) and "id" in m]
            logger.info("OpenAI models endpoint: %d models found", len(models))
            return ProviderInfo(provider_type="openai", available_models=models)
        return None
    except requests.RequestException as e:
        logger.debug("GET models failed: %s", e)
        return None
    except Exception as e:
        logger.debug("GET models parse error: %s", e)
        return None


def _try_openai_chat(base_url: str, api_key: str) -> Optional[bool]:
    """POST {base_url}/chat/completions minimal probe.

    Returns True if OpenAI-compatible, False if not, None if auth error.
    """
    url = _normalize_base_url(base_url, "openai") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        if resp.status_code in (401, 403):
            return None
        if resp.status_code in (200, 400):
            logger.info("OpenAI chat/completions confirmed (status=%d)", resp.status_code)
            return True
        return False
    except requests.RequestException:
        return False


def _try_anthropic_messages(base_url: str, api_key: str) -> Optional[bool]:
    """POST {base_url}/v1/messages minimal probe.

    Returns True if Anthropic-compatible, False if not, None if auth error.
    """
    # Anthropic SDK appends /v1/messages, so we do the same for probing
    url = base_url.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = {
        "model": "claude-haiku-4-5",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
        if resp.status_code in (401, 403):
            return None
        if resp.status_code in (200, 400):
            logger.info("Anthropic messages confirmed (status=%d)", resp.status_code)
            return True
        return False
    except requests.RequestException:
        return False


def detect_provider(base_url: str, api_key: str,
                    provider_type: str = "openai") -> ProviderInfo:
    """Detect AI provider type and available models.

    Args:
        base_url: User-provided endpoint URL.
        api_key: API key.
        provider_type: "openai" | "anthropic" | "custom" | "auto"
    """
    if not base_url or not api_key:
        return ProviderInfo(error="请填写站点 URL 和 API Key")

    # Custom → no probing
    if provider_type == "custom":
        return ProviderInfo(provider_type="custom")

    # Anthropic → probe messages endpoint only (no /models)
    if provider_type == "anthropic":
        result = _try_anthropic_messages(base_url, api_key)
        if result is True:
            return ProviderInfo(provider_type="anthropic")
        if result is None:
            return ProviderInfo(error="API Key 无效或站点地址错误，请检查后重试。")
        return ProviderInfo(error="无法连接 Anthropic 兼容端点，请检查地址和 Key")

    # OpenAI → probe models then chat/completions
    if provider_type == "openai":
        info = _try_openai_models(base_url, api_key)
        if info is not None:
            if info.error:
                return info
            if info.provider_type:
                return info

        # Models endpoint failed, try actual chat endpoint
        result = _try_openai_chat(base_url, api_key)
        if result is True:
            return ProviderInfo(provider_type="openai")
        if result is None:
            return ProviderInfo(error="API Key 无效或站点地址错误，请检查后重试。")
        return ProviderInfo(error="无法连接 OpenAI 兼容端点，请检查地址和 Key")

    # Auto → try both (legacy)
    info = _try_openai_models(base_url, api_key)
    if info is not None:
        if info.error:
            return info
        if info.provider_type:
            return info

    is_openai = _try_openai_chat(base_url, api_key)
    is_anthropic = _try_anthropic_messages(base_url, api_key)

    if is_openai is None and is_anthropic is None:
        return ProviderInfo(error="API Key 无效或站点地址错误，请检查后重试。")

    if is_openai:
        return ProviderInfo(provider_type="openai")
    if is_anthropic:
        return ProviderInfo(provider_type="anthropic")

    return ProviderInfo(error="无法自动检测 Provider 类型。请在高级选项中手动选择。")
