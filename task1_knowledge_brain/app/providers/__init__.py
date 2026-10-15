"""Providers package with factory functions for LLM and Vision models."""

from task1_knowledge_brain.app.config import settings
from task1_knowledge_brain.app.providers.base import (
    BaseLLMProvider,
    BaseVisionProvider,
    GroundedAnswer,
    LLMResponse,
    TokenUsage,
)
from task1_knowledge_brain.app.providers.mock_provider import (
    MockClaudeProvider,
    MockVisionProvider,
)


def get_llm_provider() -> BaseLLMProvider:
    """Instantiate appropriate LLM provider based on environment configuration."""
    if settings.is_mock_mode:
        return MockClaudeProvider()

    try:
        from task1_knowledge_brain.app.providers.anthropic_provider import AnthropicClaudeProvider
        return AnthropicClaudeProvider(
            api_key=settings.anthropic_api_key or "",
            model_name=settings.anthropic_model,
        )
    except Exception:
        # Fallback to mock provider if initialization fails
        return MockClaudeProvider()


def get_vision_provider() -> BaseVisionProvider:
    """Instantiate appropriate vision provider."""
    if settings.is_mock_mode:
        return MockVisionProvider()

    try:
        from task1_knowledge_brain.app.providers.anthropic_provider import AnthropicVisionProvider
        return AnthropicVisionProvider(
            api_key=settings.anthropic_api_key or "",
            model_name=settings.anthropic_model,
        )
    except Exception:
        return MockVisionProvider()


__all__ = [
    "BaseLLMProvider",
    "BaseVisionProvider",
    "GroundedAnswer",
    "LLMResponse",
    "TokenUsage",
    "MockClaudeProvider",
    "MockVisionProvider",
    "get_llm_provider",
    "get_vision_provider",
]
