"""Summarization module — factory for AI backends.

Usage:
    from .summarize import create_summarizer

    summarizer = create_summarizer(config)
    result = summarizer.summarize(messages, requester_name)
"""

import logging

from .base import AbstractSummarizer
from .models import ParticipantContribution, SummaryResult

logger = logging.getLogger(__name__)

__all__ = [
    "AbstractSummarizer",
    "ClaudeSummarizer",
    "OpenAICompatSummarizer",
    "DeepSeekSummarizer",
    "SummaryResult",
    "ParticipantContribution",
    "create_summarizer",
]


def create_summarizer(config) -> AbstractSummarizer:
    """Create the appropriate summarizer based on config.

    Args:
        config: BotConfig instance.

    Returns:
        An AbstractSummarizer implementation.

    Raises:
        ValueError: If the configured backend is unknown.
    """
    if config.ai_provider_base_url and config.ai_provider_api_key:
        provider_type = config.ai_provider_type
        if provider_type == "auto":
            from .provider_detector import detect_provider
            info = detect_provider(config.ai_provider_base_url, config.ai_provider_api_key)
            provider_type = info.provider_type if info.provider_type else "openai"
            if info.available_models and not config.ai_provider_model:
                logger.info("Auto-selected model: %s", info.available_models[0])

        model = config.ai_provider_model or "gpt-3.5-turbo"

        # Parse extra_body from JSON string
        extra_body = None
        if config.ai_provider_extra_body:
            import json
            try:
                extra_body = json.loads(config.ai_provider_extra_body)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid JSON in ai_provider_extra_body, ignoring")

        if provider_type == "anthropic":
            from .claude_backend import ClaudeSummarizer
            logger.info(
                "Creating ClaudeSummarizer (model=%s, url=%s)",
                model, config.ai_provider_base_url,
            )
            return ClaudeSummarizer(
                api_key=config.ai_provider_api_key,
                model=model,
                base_url=config.ai_provider_base_url,
                chunk_size=config.chunk_size,
            )
        else:
            # Default to OpenAI-compatible (DeepSeek, OpenAI, MiMo, etc.)
            from .deepseek_backend import OpenAICompatSummarizer
            logger.info(
                "Creating OpenAICompatSummarizer (model=%s, url=%s, extra_body=%s)",
                model, config.ai_provider_base_url, bool(extra_body),
            )
            return OpenAICompatSummarizer(
                api_key=config.ai_provider_api_key,
                model=model,
                base_url=config.ai_provider_base_url,
                chunk_size=config.chunk_size,
                extra_body=extra_body,
            )

    raise ValueError(
        "AI_PROVIDER_BASE_URL 和 AI_PROVIDER_API_KEY 未设置。"
        "请在 .env 或 Web 仪表盘中配置 AI 提供商。"
    )
