"""Abstract Base Classes and Data Contracts for AI Providers.

Enables seamless swapping between real Anthropic Claude and deterministic Mock Mode.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    """Estimated or measured token usage and pricing metrics."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class LLMResponse(BaseModel):
    """Structured response from an LLM call."""
    content: str
    model_name: str
    is_mock: bool = False
    latency_ms: float = 0.0
    usage: TokenUsage = Field(default_factory=TokenUsage)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class GroundedAnswer(BaseModel):
    """Contract for a RAG-grounded answer."""
    question: str
    answer: str
    is_grounded: bool
    is_sufficient: bool  # False if model explicitly refuses due to lack of evidence
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    confidence_score: float = 0.0
    needs_human_review: bool = False
    model_name: str = "mock-grounder"
    is_mock: bool = False
    latency_ms: float = 0.0
    usage: TokenUsage = Field(default_factory=TokenUsage)


class BaseLLMProvider(ABC):
    """Abstract interface for LLM operations."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Generate text completion."""
        pass

    @abstractmethod
    async def answer_grounded(
        self,
        question: str,
        retrieved_contexts: List[Dict[str, Any]],
    ) -> GroundedAnswer:
        """Produce an answer grounded strictly in retrieved context chunks."""
        pass


class BaseVisionProvider(ABC):
    """Abstract interface for analyzing article screenshots & images."""

    @abstractmethod
    async def describe_image(
        self,
        image_url: str,
        article_title: str,
        alt_text: str = "",
    ) -> str:
        """Extract information, UI elements, or explanatory text from an image."""
        pass
