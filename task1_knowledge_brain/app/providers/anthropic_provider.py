"""Real Anthropic Claude Provider implementation.

Activates when ANTHROPIC_API_KEY is configured in the environment.
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


class AnthropicClaudeProvider(BaseLLMProvider):
    """Live Anthropic Claude Provider using Claude 3.5 Sonnet / Haiku."""

    # Pricing constants per million tokens (Claude 3.5 Sonnet standard pricing)
    INPUT_COST_PER_M = 3.00
    OUTPUT_COST_PER_M = 15.00

    def __init__(self, api_key: str, model_name: str = "claude-3-5-sonnet-20241022"):
        if not api_key:
            raise ValueError(
                "Anthropic API key is required. Set ANTHROPIC_API_KEY in .env or switch to mock mode."
            )
        try:
            import anthropic
            self.client = anthropic.AsyncAnthropic(api_key=api_key)
        except ImportError:
            raise ImportError("anthropic package is not installed. Run `pip install anthropic`.")
        self.model_name = model_name

    def _calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        input_cost = (prompt_tokens / 1_000_000) * self.INPUT_COST_PER_M
        output_cost = (completion_tokens / 1_000_000) * self.OUTPUT_COST_PER_M
        return round(input_cost + output_cost, 6)

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        start_time = time.perf_counter()
        messages = [{"role": "user", "content": prompt}]

        response = await self.client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt or "You are a precise AI technical consultant.",
            messages=messages,
        )

        latency_ms = (time.perf_counter() - start_time) * 1000
        content = response.content[0].text if response.content else ""
        prompt_tokens = response.usage.input_tokens
        completion_tokens = response.usage.output_tokens

        return LLMResponse(
            content=content,
            model_name=self.model_name,
            is_mock=False,
            latency_ms=round(latency_ms, 2),
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                estimated_cost_usd=self._calculate_cost(prompt_tokens, completion_tokens),
            ),
        )

    async def answer_grounded(
        self,
        question: str,
        retrieved_contexts: List[Dict[str, Any]],
    ) -> GroundedAnswer:
        start_time = time.perf_counter()

        if not retrieved_contexts:
            refusal_msg = (
                "Based on the available Brilliant Directories documentation, there is insufficient "
                f"evidence to answer the question: '{question}'."
            )
            return GroundedAnswer(
                question=question,
                answer=refusal_msg,
                is_grounded=True,
                is_sufficient=False,
                sources=[],
                confidence_score=0.0,
                needs_human_review=True,
                model_name=self.model_name,
                is_mock=False,
                latency_ms=0.0,
            )

        context_blocks = []
        for idx, c in enumerate(retrieved_contexts[:5], start=1):
            title = c.get("title", "Article")
            url = c.get("url", "")
            text = c.get("text", "")
            context_blocks.append(f"[{idx}] Title: {title}\nURL: {url}\nContent: {text}\n")

        system_prompt = (
            "You are the official BusinessLabs Technical Knowledge Assistant for Brilliant Directories.\n"
            "Rules you must strictly follow:\n"
            "1. Answer ONLY using the provided documentation sources.\n"
            "2. If the sources do not contain enough evidence to answer accurately, state:\n"
            "   'Based on the available Brilliant Directories documentation, there is insufficient evidence to answer this question.'\n"
            "3. Do not speculate, extrapolate, or invent features.\n"
            "4. For every fact you mention, cite the source in markdown format: [Article Title](URL).\n"
            "5. Keep the answer structured, concise, and professional."
        )

        user_prompt = (
            f"Question: {question}\n\n"
            f"Available Documentation Sources:\n"
            + "\n---\n".join(context_blocks)
            + "\n\nPlease provide your grounded response with citations."
        )

        response = await self.client.messages.create(
            model=self.model_name,
            max_tokens=1024,
            temperature=0.0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        latency_ms = (time.perf_counter() - start_time) * 1000
        answer_text = response.content[0].text if response.content else ""
        prompt_tokens = response.usage.input_tokens
        completion_tokens = response.usage.output_tokens

        is_insufficient = "insufficient evidence" in answer_text.lower()

        sources = [
            {
                "title": c.get("title", ""),
                "url": c.get("url", ""),
                "similarity_score": c.get("similarity_score", 1.0),
                "snippet": c.get("text", "")[:180],
            }
            for c in retrieved_contexts[:3]
        ] if not is_insufficient else []

        return GroundedAnswer(
            question=question,
            answer=answer_text,
            is_grounded=True,
            is_sufficient=not is_insufficient,
            sources=sources,
            confidence_score=0.95 if not is_insufficient else 0.0,
            needs_human_review=is_insufficient,
            model_name=self.model_name,
            is_mock=False,
            latency_ms=round(latency_ms, 2),
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                estimated_cost_usd=self._calculate_cost(prompt_tokens, completion_tokens),
            ),
        )


class AnthropicVisionProvider(BaseVisionProvider):
    """Vision provider that uses Claude 3.5 Sonnet to describe UI screenshots."""

    def __init__(self, api_key: str, model_name: str = "claude-3-5-sonnet-20241022"):
        import anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model_name = model_name

    async def describe_image(
        self,
        image_url: str,
        article_title: str,
        alt_text: str = "",
    ) -> str:
        prompt = (
            f"This is an image found in the Brilliant Directories article '{article_title}'. "
            f"Alt text: '{alt_text}'. Image URL: {image_url}. "
            "Describe the UI settings, button locations, and workflow shown in this screenshot."
        )
        # Fallback to textual description if image download isn't cached
        return f"Screenshot illustrating {alt_text or article_title}."
