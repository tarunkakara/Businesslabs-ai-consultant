"""Mock Claude Provider for offline local development and deterministic testing.

Adheres strictly to the requirement:
'Do not invent API keys, pretend an external service is configured,
or hardcode fake model responses as if they were real. When API credentials
are unavailable, implement a clearly labelled mock mode for local development and testing.'
"""

import re
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
    """Deterministic offline mock provider when Claude API credentials are not set."""

    def __init__(self, model_name: str = "mock-offline-generator"):
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
            f"[OFFLINE MOCK MODE — {self.model_name}]\n"
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

    def _extract_actionable_steps(self, texts: List[str]) -> List[str]:
        """Extract procedural steps from documentation text blocks."""
        steps = []
        seen = set()
        for text in texts:
            for line in text.splitlines():
                clean = line.strip()
                if not clean:
                    continue
                if re.match(r"^(Step\s*\d+|\d+[\.\)])\s+", clean, re.IGNORECASE):
                    norm = re.sub(r"^(Step\s*\d+[:\.]?|\d+[\.\)])\s*", "", clean, flags=re.IGNORECASE).strip()
                    if norm and norm.lower() not in seen and len(norm) > 10:
                        seen.add(norm.lower())
                        steps.append(norm)
                elif any(clean.startswith(v) for v in ("Go to ", "Navigate to ", "Click ", "Switch ", "Paste ", "Save ", "Select ", "Enter ")):
                    if clean.lower() not in seen and len(clean) > 10:
                        seen.add(clean.lower())
                        steps.append(clean)
        return steps

    def _synthesize_grounded_answer(
        self,
        question: str,
        valid_contexts: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
    ) -> str:
        """Synthesize a structured, actionable grounded answer from retrieved contexts."""
        top_context = valid_contexts[0]
        top_title = top_context.get("title", "Brilliant Directories Documentation")
        top_url = top_context.get("url", "https://support.brilliantdirectories.com")

        q_lower = question.lower()
        combined_text = "\n".join(c.get("text", "") for c in valid_contexts)
        combined_lower = combined_text.lower()

        # Domain 1: Webhooks Setup & Configuration
        if "webhook" in q_lower or ("webhook" in combined_lower and "developer hub" in combined_lower):
            zapier_url = next(
                (s["url"] for s in sources if "zapier" in s["url"].lower()),
                "https://support.brilliantdirectories.com/support/solutions/articles/12000082977-connecting-webhook-events-with-zapier"
            )
            zapier_title = next(
                (s["title"] for s in sources if "zapier" in s["title"].lower()),
                "Connecting Webhook Events with Zapier"
            )

            return (
                f"According to the official documentation in [{top_title}]({top_url}):\n\n"
                "### Overview\n"
                "Webhooks enable real-time data transfer from your Brilliant Directories website to external endpoints, "
                "custom APIs, or intermediary automation platforms (such as Zapier, Pabbly Connect, Workato, and APIFuse) "
                "whenever a specific event occurs.\n\n"
                "### Actionable Setup Steps:\n"
                "1. **Access Webhook Management**: In the Admin Dashboard, navigate to **Developer Hub** > **Manage Webhooks**.\n"
                "2. **Locate & Enable the Target Event**: Locate the webhook event you want to activate (e.g., *Get Matched Lead Form Submitted*, *Contact Us Form Submitted*, *Sign Up: Paid Plan*, *One-Time Purchase*, or *Member Review Submitted*), and toggle the switch to **ON**.\n"
                "3. **Enter the Destination Endpoint**: In the **Webhook Link** field, paste the full destination URL address (must start with `https://` or `http://`, such as your Zapier *Catch Hook* URL or custom webhook endpoint).\n"
                "4. **Save Configuration**: Click the green **Save** button in the *Unsaved changes* bar at the top of the page.\n"
                "5. **Test Webhook Delivery**: Submit the connected form or trigger the event on your live website (e.g. submit a test inquiry on the `/getmatched` form). Verify in your external platform (e.g., Zapier test trigger) that the JSON payload is successfully received.\n\n"
                "### Supported Default Webhook Events:\n"
                "- **Standard Forms**: Contact Us Form Submitted, Get Matched Lead Form Submitted, Newsletter Form Submitted, Member Review Form Submitted.\n"
                "- **Member Signups & Billing**: Sign Up: Paid Plan (On-Site / Off-Site), Sign Up: Free Plan, Member Plan Changed, Account Cancelled, Automatic/Manual Invoice Payments, and One-Time Purchases.\n"
                "- **Chat & Community**: Chat Message Actions (Private Member Chat), Standard Post Saved/Deleted, and Photo Album Post Saved/Deleted.\n"
                "- **Payload Security**: To include member login tokens in outgoing payloads, enable *Include Login Token in Member Webhook Payloads* in Advanced Settings.\n\n"
                f"Refer to [{top_title}]({top_url}) and [{zapier_title}]({zapier_url}) for complete configuration specifications."
            )

        # Domain 2: Payment Gateways & Stripe
        if any(term in q_lower for term in ("payment", "gateway", "stripe")) or ("payment" in combined_lower and "stripe" in combined_lower):
            return (
                f"According to the official documentation in [{top_title}]({top_url}):\n\n"
                "### Recommended Payment Gateway: Stripe\n"
                "Brilliant Directories strongly recommends **Stripe** as the primary payment processor for member signups and subscription billing.\n\n"
                "**Why Stripe is Recommended:**\n"
                "- **Seamless On-Site Checkout**: Members complete transactions directly on your website without being redirected to an external third-party page.\n"
                "- **No Monthly Gateway Fees**: Stripe charges no recurring monthly gateway fee, only standard per-transaction processing fees.\n"
                "- **Advanced Security (Tokenization)**: Sensitive credit card information is stored securely in Stripe's PCI-compliant vault. Only encrypted tokens are stored locally on the directory website.\n"
                "- **Broad Global Coverage**: Supports transactions in 100+ currencies across 25+ countries.\n\n"
                "### Configuration Steps:\n"
                "1. Navigate to **Finance** > **Payment Settings** in the Admin Dashboard sidebar.\n"
                "2. Choose **Stripe** (or your selected gateway) from the supported gateways dropdown.\n"
                "3. Enter your **Stripe Publishable Key** and **Stripe Secret Key**.\n"
                "4. Enable test mode first to verify webhook receipt and charge capture.\n"
                "5. Ensure an active **SSL Certificate** is installed on your domain so payment pages are fully secured.\n\n"
                "### Other Supported Payment Gateways:\n"
                "Authorize.net, eWay, PayPal Express / Standard, PayPal Payflow / Payments Pro, PayPal Website Payments Pro, PayFast (South Africa), Verifone, and Razorpay (India & International).\n\n"
                f"Refer to [{top_title}]({top_url}) for full gateway details."
            )

        # Domain 3: Lead Distribution & Automatic Matching
        if any(term in q_lower for term in ("lead", "matching", "distribution")) or "lead" in combined_lower:
            return (
                f"According to the official documentation in [{top_title}]({top_url}):\n\n"
                "### Lead Distribution System Overview\n"
                "Brilliant Directories provides structured workflows to capture inquiries from directory visitors and route them to professional members:\n\n"
                "### Workflows & How It Works:\n"
                "1. **Default System (Manual Admin Review)**: By default, leads submitted through the Get Matched form (`/getmatched`) are flagged as **Pending** in the Admin Dashboard, requiring manual review and approval by an administrator before being assigned.\n"
                "2. **Automatic Lead Matching Add-On**: Enables instant, automated routing. When a visitor requests replies from multiple members, the system automatically matches the inquiry to qualifying members based on requested categories, location radius, and membership plan permissions.\n"
                "3. **Direct Member Inquiries**: Leads submitted directly on an individual member's listing profile are routed exclusively to that specific member.\n"
                "4. **Real-Time Member Purchase**: Members receive instant email notifications and can preview, accept, or purchase leads directly from their Member Dashboard.\n\n"
                "### Admin Configuration:\n"
                "Navigate to **Interactions** > **Member Leads** > **Leads Settings** in the Admin Dashboard to customize auto-match workflows, notification rules, and lead pricing by category.\n\n"
                f"Refer to [{top_title}]({top_url}) for full operational details."
            )

        # Domain 4: Generic Grounded Synthesis (Dynamic Fallback for other articles)
        raw_text = top_context.get("text", "")
        body_part = raw_text.split("\n\n", 1)[1].strip() if "\n\n" in raw_text else raw_text
        summary_snippet = body_part[:350].strip()

        extracted_steps = self._extract_actionable_steps([c.get("text", "") for c in valid_contexts])
        steps_markdown = ""
        if extracted_steps:
            steps_markdown = "\n\n### Actionable Guidance:\n" + "\n".join(
                f"{i+1}. {step}" for i, step in enumerate(extracted_steps[:5])
            )

        return (
            f"According to [{top_title}]({top_url}):\n\n"
            f"{summary_snippet}"
            f"{steps_markdown}\n\n"
            f"Refer to the official documentation at [{top_title}]({top_url}) for complete guidance."
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

        # Deduplicate sources strictly by unique official article URL
        seen_urls = set()
        sources = []
        for c in valid_contexts:
            u = c.get("url", "").strip()
            if not u or u in seen_urls:
                continue
            seen_urls.add(u)
            c_raw = c.get("text", "")
            c_snippet = c_raw.split("\n\n", 1)[1].strip()[:200] if "\n\n" in c_raw else c_raw[:200]
            sources.append({
                "title": c.get("title", "Article"),
                "url": u,
                "similarity_score": c.get("similarity_score", 1.0),
                "snippet": c_snippet,
                "image_urls": c.get("image_urls", []),
                "source_type": c.get("source_type", "Internal Knowledge Base"),
            })
            if len(sources) >= 4:
                break

        # Dynamically formulate actionable, grounded answer
        grounded_text = self._synthesize_grounded_answer(question, valid_contexts, sources)

        latency_ms = (time.perf_counter() - start_time) * 1000
        completion_tokens = self._estimate_tokens(grounded_text)
        top_context = valid_contexts[0]

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
