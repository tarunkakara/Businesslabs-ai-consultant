"""Email Response Drafter with Configurable Company Voice."""

import uuid
from typing import Any, Dict, List, Optional
from task2_inbox_agent.app.config import settings
from task2_inbox_agent.app.kb_client import KnowledgeBrainClient
from task2_inbox_agent.app.models import (
    CompanyVoice,
    CustomerEmail,
    DraftResponse,
    DraftStatus,
    EmailCategory,
    EmailClassification,
)


class ResponseDrafter:
    """Drafts customer replies in configurable company voice, incorporating Knowledge Brain facts."""

    def __init__(self, kb_client: Optional[KnowledgeBrainClient] = None):
        self.kb_client = kb_client or KnowledgeBrainClient()

    async def draft_response(
        self,
        email: CustomerEmail,
        classification: EmailClassification,
        voice: Optional[CompanyVoice] = None,
    ) -> DraftResponse:
        """Generate response draft tailored to classification, voice, and KB facts."""
        selected_voice = voice or settings.default_voice
        first_name = email.sender_name.split()[0] if email.sender_name else "Valued Customer"

        kb_sources: List[Dict[str, Any]] = []
        kb_answer_snippet = ""

        # Step 1: Query Knowledge Brain if technical / product lookup is needed
        if classification.requires_kb_lookup:
            kb_res = await self.kb_client.query(email.subject + " " + email.body[:150])
            if kb_res.get("is_sufficient"):
                kb_sources = kb_res.get("sources", [])
                kb_answer_snippet = kb_res.get("answer", "")

        # Step 2: Build greeting based on voice
        greeting = self._build_greeting(first_name, selected_voice)

        # Step 3: Build body content by category & voice
        body_content = self._build_category_body(
            category=classification.category,
            email=email,
            voice=selected_voice,
            kb_snippet=kb_answer_snippet,
            sources=kb_sources,
        )

        # Step 4: Build closing
        closing = self._build_closing(selected_voice)

        full_draft = f"{greeting}\n\n{body_content}\n\n{closing}"
        draft_id = f"DFT-{uuid.uuid4().hex[:8].upper()}"

        return DraftResponse(
            draft_id=draft_id,
            email_id=email.id,
            subject=f"Re: {email.subject}",
            original_draft=full_draft,
            voice=selected_voice,
            kb_sources_cited=kb_sources,
            confidence_score=classification.confidence,
            status=DraftStatus.PENDING_REVIEW,
        )

    def _build_greeting(self, name: str, voice: CompanyVoice) -> str:
        if voice == CompanyVoice.EMPATHETIC:
            return f"Dear {name},\n\nThank you for reaching out to us. We truly appreciate your patience."
        elif voice == CompanyVoice.CONCISE:
            return f"Hi {name},"
        elif voice == CompanyVoice.EXECUTIVE:
            return f"Dear {name},\n\nThank you for contacting BusinessLabs Advisory."
        return f"Hello {name},\n\nThank you for reaching out to the BusinessLabs Support Team."

    def _build_category_body(
        self,
        category: EmailCategory,
        email: CustomerEmail,
        voice: CompanyVoice,
        kb_snippet: str,
        sources: List[Dict[str, Any]],
    ) -> str:
        if category == EmailCategory.REFUND:
            if voice == CompanyVoice.EMPATHETIC:
                return (
                    "I understand you are requesting a refund, and I sincerely apologize for any confusion or inconvenience with your billing.\n\n"
                    "I have flagged your request directly with our billing supervisor for expedited review. "
                    "Approved refunds are typically credited back to your original payment method within 3 to 5 business days."
                )
            return (
                "Regarding your refund inquiry: We have received your request and logged it with our finance operations team. "
                "Refunds are processed according to our policy within 3-5 business days upon review."
            )

        elif category == EmailCategory.COMPLAINT:
            if voice == CompanyVoice.EMPATHETIC:
                return (
                    "I am deeply sorry to hear about the difficulties you have encountered. We hold our platform to the highest standards, "
                    "and it is clear we fell short in this instance.\n\n"
                    "Our senior engineering lead has been notified of your report and is actively investigating the incident. "
                    "We are prioritizing this to ensure full resolution as quickly as possible."
                )
            return (
                "We take your feedback very seriously. Our technical escalation team has opened an investigation into the reported issue. "
                "We will follow up directly as soon as the diagnostic findings are available."
            )

        elif category == EmailCategory.QUOTE:
            return (
                "Thank you for your interest in our engineering and consulting services. We would be delighted to assist with your project.\n\n"
                "To ensure an accurate quote and timeline, our solutions architect will review your technical specifications. "
                "We can schedule an introductory 20-minute scoping call, or we can send a written preliminary estimate within 24 hours."
            )

        elif category == EmailCategory.PROJECT_REQUEST:
            return (
                "We have received your custom project request. Our engineering team specializes in custom Brilliant Directories development, "
                "API integrations, and complex data migrations.\n\n"
                "We will review your requirements against existing platform capabilities and follow up with a detailed Scope of Work (SOW)."
            )

        elif category == EmailCategory.FOLLOW_UP:
            return (
                "Thank you for following up with us. We are currently checking the status of your ticket with the assigned team member.\n\n"
                "We will provide a detailed progress update within the next few hours."
            )

        else:  # PRODUCT_QUESTION
            if kb_snippet:
                citation_text = ""
                if sources:
                    citation_text = "\n\nHelpful Reference Articles:\n" + "\n".join(
                        f"- [{s.get('title', 'Guide')}]({s.get('url', '')})" for s in sources[:2]
                    )
                return (
                    f"Regarding your technical question:\n\n{kb_snippet}{citation_text}\n\n"
                    "Please let us know if you need any further clarification as you configure your setup."
                )
            return (
                "Regarding your inquiry: Brilliant Directories provides settings for this in the Admin Dashboard. "
                "You can access the relevant module from the left-hand navigation sidebar under General Settings or Developer Tools."
            )

    def _build_closing(self, voice: CompanyVoice) -> str:
        if voice == CompanyVoice.CONCISE:
            return "Best,\nBusinessLabs Support"
        elif voice == CompanyVoice.EXECUTIVE:
            return "Warm regards,\n\nExecutive Advisory Group\nBusinessLabs AI Tech Consulting"
        elif voice == CompanyVoice.EMPATHETIC:
            return "Warmest regards and here to help,\n\nCustomer Care Specialist\nBusinessLabs"
        return "Best regards,\n\nBusinessLabs AI Technical Support"
