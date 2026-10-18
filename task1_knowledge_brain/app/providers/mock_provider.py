"""Mock Claude Provider for offline local development and deterministic testing.

Adheres strictly to the requirement:
'Do not invent API keys, pretend an external service is configured,
or hardcode fake model responses as if they were real. When API credentials
are unavailable, implement a clearly labelled mock mode for local development and testing.'
"""

import time
from typing import Any, Dict, List, Optional
from task1_knowledge_brain.app.providers.base import (
    BaseLLMProvider,
    BaseVisionProvider,
    GroundedAnswer,
    LLMResponse,
    TokenUsage,
)


class MockClaudeProvider(BaseLLMProvider):
    """Deterministic mock provider when Claude API credentials are not set."""

    def __init__(self, model_name: str = "mock-claude-3-5-sonnet"):
        self.model_name = model_name

    def _estimate_tokens(self, text: str) -> int:
        """Rough token heuristic (~4 characters per token)."""
        return max(1, len(text) // 4)

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        start_time = time.perf_counter()

        prompt_tokens = self._estimate_tokens(prompt + (system_prompt or ""))
        mock_reply = (
            f"[MOCK MODE — {self.model_name}]\n"
            f"Processed prompt of {len(prompt)} characters. "
            "To enable live Claude inferences, configure ANTHROPIC_API_KEY in .env."
        )
        completion_tokens = self._estimate_tokens(mock_reply)
        latency_ms = (time.perf_counter() - start_time) * 1000

        return LLMResponse(
            content=mock_reply,
            model_name=self.model_name,
            is_mock=True,
            latency_ms=round(latency_ms, 2),
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                estimated_cost_usd=0.0,
            ),
            metadata={"offline_mode": True},
        )

    async def answer_grounded(
        self,
        question: str,
        retrieved_contexts: List[Dict[str, Any]],
    ) -> GroundedAnswer:
        start_time = time.perf_counter()
        prompt_text = question + " " + " ".join(c.get("text", "") for c in retrieved_contexts)
        prompt_tokens = self._estimate_tokens(prompt_text)

        # Refusal check: If no contexts or if all contexts have poor similarity score (<0.30)
        valid_contexts = [
            c for c in retrieved_contexts
            if c.get("similarity_score", 1.0) >= 0.30 and c.get("text", "").strip()
        ]

        if not valid_contexts:
            latency_ms = (time.perf_counter() - start_time) * 1000
            refusal_text = (
                "Based on the available Brilliant Directories documentation, there is insufficient "
                f"evidence to answer the question: '{question}'. "
                "This query has been logged for human expert review."
            )
            return GroundedAnswer(
                question=question,
                answer=refusal_text,
                is_grounded=True,
                is_sufficient=False,
                sources=[],
                confidence_score=0.0,
                needs_human_review=True,
                model_name=self.model_name,
                is_mock=True,
                latency_ms=round(latency_ms, 2),
                usage=TokenUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=self._estimate_tokens(refusal_text),
                    total_tokens=prompt_tokens + self._estimate_tokens(refusal_text),
                    estimated_cost_usd=0.0,
                ),
            )

        # Build grounded response strictly using retrieved sources
        top_context = valid_contexts[0]
        title = top_context.get("title", "Brilliant Directories Documentation")
        url = top_context.get("url", "https://support.brilliantdirectories.com")
        raw_text = top_context.get("text", "")
        if "\n\n" in raw_text:
            body_part = raw_text.split("\n\n", 1)[1].strip()
            summary_snippet = body_part[:350].strip()
        else:
            summary_snippet = raw_text[:350].strip()

        grounded_text = (
            f"According to [{title}]({url}):\n\n"
            f"> \"{summary_snippet}...\"\n\n"
            f"Refer to the official documentation at [{title}]({url}) for detailed step-by-step guidance."
        )

        sources = []
        for c in valid_contexts[:3]:
            c_raw = c.get("text", "")
            c_snippet = c_raw.split("\n\n", 1)[1].strip()[:180] if "\n\n" in c_raw else c_raw[:180]
            sources.append({
                "title": c.get("title", "Article"),
                "url": c.get("url", ""),
                "similarity_score": c.get("similarity_score", 1.0),
                "snippet": c_snippet,
                "image_urls": c.get("image_urls", []),
            })

        latency_ms = (time.perf_counter() - start_time) * 1000
        completion_tokens = self._estimate_tokens(grounded_text)

        return GroundedAnswer(
            question=question,
            answer=grounded_text,
            is_grounded=True,
            is_sufficient=True,
            sources=sources,
            confidence_score=round(top_context.get("similarity_score", 0.90), 2),
            needs_human_review=False,
            model_name=self.model_name,
            is_mock=True,
            latency_ms=round(latency_ms, 2),
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                estimated_cost_usd=0.0,
            ),
        )


class MockVisionProvider(BaseVisionProvider):
    """Deterministic mock vision model for offline document image understanding."""

    async def describe_image(
        self,
        image_url: str,
        article_title: str,
        alt_text: str = "",
    ) -> str:
        """Produce structured visual description and metadata."""
        if alt_text:
            return f"Screenshot from '{article_title}': UI element illustrating {alt_text}."
        filename = image_url.split("/")[-1].split("?")[0]
        return f"Documentation screenshot from '{article_title}' (asset: {filename})."
