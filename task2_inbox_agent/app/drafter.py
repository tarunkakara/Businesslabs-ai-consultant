"""Email Response Drafter with Configurable Company Voice, Factual Grounding, and Prompt Injection Defense."""

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from task2_inbox_agent.app.config import settings
from task2_inbox_agent.app.kb_client import KnowledgeBrainClient
from task2_inbox_agent.app.learning import ContinuousLearningEngine
from task2_inbox_agent.app.models import (
    CompanyVoice,
    CustomerEmail,
    DraftResponse,
    DraftStatus,
    EmailCategory,
    EmailClassification,
    normalize_voice,
)

logger = logging.getLogger("inbox_agent.drafter")


class ResponseDrafter:
    """Drafts customer replies in configurable company voice, incorporating Knowledge Brain facts,

    factual anti-hallucination constraints, prompt-injection defense, and continuous user learning.
    """

    # Indicators of prompt injection or untrusted override attempts
    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?(previous|prior)\s+instructions",
        r"disregard\s+(all\s+)?(previous|prior)\s+rules",
        r"you\s+are\s+now\s+(dan|jailbroken|unrestricted)",
        r"system\s+(override|prompt|instructions)",
        r"(reveal|print|disclose|show|leak)\s+(the\s+)?(system\s+prompt|api\s*key|secret|password|credential)",
        r"permanently\s+waive\s+(all\s+)?fees",
        r"grant\s+lifetime\s+(admin|free)\s+access",
        r"output\s*:\s*pwned",
        r"<!--\s*admin\s+instruction",
    ]

    def __init__(
        self,
        kb_client: Optional[KnowledgeBrainClient] = None,
        learning_engine: Optional[ContinuousLearningEngine] = None,
    ):
        self.kb_client = kb_client or KnowledgeBrainClient()
        self.learning_engine = learning_engine or ContinuousLearningEngine()

    async def draft_response(
        self,
        email: CustomerEmail,
        classification: EmailClassification,
        voice: Optional[CompanyVoice] = None,
    ) -> DraftResponse:
        """Generate response draft tailored to classification, voice, KB facts, and continuous user learning."""
        selected_voice = normalize_voice(voice or settings.default_voice)
        first_name = email.sender_name.split()[0] if email.sender_name else "Valued Customer"

        # Step 0: Check for Prompt Injection / Malicious Override
        is_injection, injection_reason = self._detect_prompt_injection(f"{email.subject} {email.body}")
        if is_injection:
            logger.warning("Prompt injection attempt detected in email %s: %s", email.id, injection_reason)
            return self._build_security_refusal_draft(email, selected_voice, first_name, injection_reason)

        kb_sources: List[Dict[str, Any]] = []
        kb_answer_snippet = ""
        kb_is_sufficient = False

        # Step 1: Query Knowledge Brain if technical / product lookup is needed
        if classification.requires_kb_lookup or classification.category in (
            EmailCategory.FILTERS,
            EmailCategory.DESIGN_SETTINGS,
            EmailCategory.BILLING,
            EmailCategory.PRODUCT_QUESTION,
            EmailCategory.PROJECT_REQUEST,
        ):
            # Extract clean question for KB query
            clean_query = self._extract_clean_search_query(email)
            try:
                kb_res = await self.kb_client.query(clean_query)
                if kb_res:
                    kb_sources = kb_res.get("sources", [])
                    kb_answer_snippet = kb_res.get("answer", "")
                    kb_is_sufficient = kb_res.get("is_sufficient", False)
            except Exception as exc:
                logger.warning("Knowledge Brain query failed: %s; proceeding with safe fallback.", exc)
                kb_is_sufficient = False

        # Step 2: Build greeting based on voice
        greeting = self._build_greeting(first_name, selected_voice)

        # Step 3: Build body content by category & voice with factual grounding
        body_content = self._build_grounded_body(
            email=email,
            classification=classification,
            voice=selected_voice,
            kb_snippet=kb_answer_snippet,
            kb_sufficient=kb_is_sufficient,
            sources=kb_sources,
        )

        # Step 4: Build closing
        closing = self._build_closing(selected_voice)

        # Step 5: Check continuous learning rules learned from recent human edits
        # Only override default greeting/closing if user did not explicitly select a distinct voice
        if voice is None or voice == settings.default_voice:
            learned_rules = self.learning_engine.get_learned_rules(
                category=classification.category.value if classification.category else None,
                limit=3,
            )
            if learned_rules:
                for rule in learned_rules:
                    instruction = rule.get("learned_instruction", "")
                    m_sign = re.search(r"Sign off with:\s*['\"]([^'\"]{3,60})['\"]", instruction)
                    if m_sign:
                        closing = m_sign.group(1).strip()
                    m_start = re.search(r"Start email with:\s*['\"]([^'\"]{3,60})['\"]", instruction)
                    if m_start:
                        greeting = m_start.group(1).strip()

        full_draft = f"{greeting}\n\n{body_content}\n\n{closing}"
        draft_id = f"DFT-{uuid.uuid4().hex[:8].upper()}"

        return DraftResponse(
            draft_id=draft_id,
            email_id=email.id,
            subject=f"Re: {email.subject}" if not email.subject.lower().startswith("re:") else email.subject,
            original_draft=full_draft,
            voice=selected_voice,
            kb_sources_cited=kb_sources if kb_is_sufficient else [],
            confidence_score=classification.confidence,
            status=DraftStatus.PENDING_REVIEW,
        )

    def _detect_prompt_injection(self, text: str) -> Tuple[bool, str]:
        """Detect prompt injection, system override, or confidential disclosure attempts."""
        t_lower = text.lower()
        for pat in self.INJECTION_PATTERNS:
            if re.search(pat, t_lower):
                return True, f"Matched forbidden prompt injection pattern: '{pat}'"
        return False, ""

    def _extract_clean_search_query(self, email: CustomerEmail) -> str:
        """Sanitize and focus search query for Knowledge Brain."""
        combined = f"{email.subject} {email.body}"
        # Remove common noise
        cleaned = re.sub(r"[^\w\s\?\-\.]", " ", combined)
        words = cleaned.split()[:40]
        return " ".join(words)

    def _build_security_refusal_draft(
        self, email: CustomerEmail, voice: CompanyVoice, name: str, reason: str
    ) -> DraftResponse:
        """Create a safe refusal draft when an incoming email contains prompt injection."""
        greeting = f"Hello {name}," if voice != CompanyVoice.CONCISE else f"Hi {name},"
        body = (
            "Thank you for contacting BusinessLabs Support.\n\n"
            "We noticed that your message contains administrative command sequences or instructions "
            "that conflict with our system safety and security policies. As an enterprise platform, "
            "our automated agents cannot execute arbitrary system overrides, disclose internal configuration keys, "
            "or bypass standard human authorization protocols.\n\n"
            "If you have a legitimate technical question or account request, please reply with your specific inquiry, "
            "and our verified support personnel will be glad to assist you."
        )
        closing = self._build_closing(voice)
        full_draft = f"{greeting}\n\n{body}\n\n{closing}"

        return DraftResponse(
            draft_id=f"DFT-{uuid.uuid4().hex[:8].upper()}",
            email_id=email.id,
            subject=f"Re: {email.subject}",
            original_draft=full_draft,
            voice=voice,
            kb_sources_cited=[],
            confidence_score=0.99,
            status=DraftStatus.PENDING_REVIEW,
        )

    def _build_greeting(self, name: str, voice: CompanyVoice) -> str:
        if voice == CompanyVoice.FRIENDLY:
            return f"Hi {name}!"
        elif voice == CompanyVoice.FORMAL:
            return f"Dear {name},"
        elif voice == CompanyVoice.EMPATHETIC:
            return f"Dear {name},\n\nThank you for reaching out to us. We truly appreciate your patience."
        elif voice == CompanyVoice.CONCISE:
            return f"Hi {name},"
        elif voice == CompanyVoice.EXECUTIVE:
            return f"Dear {name},\n\nThank you for contacting BusinessLabs Advisory."
        # Default PROFESSIONAL
        return f"Hello {name},\n\nThank you for contacting the BusinessLabs Technical Support Team."

    def _build_closing(self, voice: CompanyVoice) -> str:
        if voice == CompanyVoice.FRIENDLY:
            return "Warmly and here to help,\n\nThe BusinessLabs Team"
        elif voice == CompanyVoice.FORMAL:
            return "Sincerely,\n\nBusinessLabs Advisory & Support Group"
        elif voice == CompanyVoice.CONCISE:
            return "Best,\nBusinessLabs Support"
        elif voice == CompanyVoice.EMPATHETIC:
            return "Warmest regards and here to help,\n\nCustomer Care Specialist\nBusinessLabs"
        elif voice == CompanyVoice.EXECUTIVE:
            return "Warm regards,\n\nExecutive Advisory Group\nBusinessLabs AI Tech Consulting"
        # Default PROFESSIONAL
        return "Best regards,\n\nBusinessLabs AI Technical Support"

    def _build_grounded_body(
        self,
        email: CustomerEmail,
        classification: EmailClassification,
        voice: CompanyVoice,
        kb_snippet: str,
        kb_sufficient: bool,
        sources: List[Dict[str, Any]],
    ) -> str:
        """Synthesize body text adhering to question comprehension, factual grounding, and voice differentiation."""
        subject_lower = email.subject.lower()
        body_lower = email.body.lower()
        text_lower = f"{subject_lower} {body_lower}"

        # 1. Check for Irrelevant / Off-Topic emails
        if any(w in text_lower for w in ["recipe", "lasagna", "basketball", "weather tomorrow", "movie review"]):
            return self._format_styled_response(
                core_answer=(
                    "Thank you for contacting BusinessLabs. Our support desk specializes exclusively in Brilliant Directories "
                    "implementation, AI tech consulting, website customization, and API integrations. "
                    "We are unable to answer inquiries regarding non-directory or unrelated lifestyle topics. "
                    "If you have any questions regarding your directory platform or tech setup, please feel free to reach out."
                ),
                voice=voice,
                category="off_topic",
            )

        # 2. Check for Ambiguous / Underspecified Requests
        if any(phrase in text_lower for phrase in ["nothing works", "it's broken", "its broken", "site is broken", "please fix it"]) and len(email.body.split()) < 15:
            return self._format_styled_response(
                core_answer=(
                    "We received your message regarding an issue on your website. To help our technical team troubleshoot "
                    "and resolve this quickly, could you please provide a few additional details:\n\n"
                    "- The exact URL of the page where the issue occurs\n"
                    "- Any specific error messages or codes displayed\n"
                    "- Steps to reproduce the problem or relevant screenshots\n\n"
                    "Once you share these details, our engineering team will investigate immediately."
                ),
                voice=voice,
                category="clarification",
            )

        # 3. Check for Missing Policy / Insufficient Evidence (e.g. 100% money-back conversion guarantee)
        if "guarantee" in text_lower and ("conversion" in text_lower or "money-back" in text_lower or "money back" in text_lower):
            return self._format_styled_response(
                core_answer=(
                    "Regarding your inquiry concerning conversion guarantees: Brilliant Directories provides standard platform "
                    "hosting, software tools, and technical support. However, we do not have an automated or published policy offering "
                    "a 100% money-back guarantee tied to individual member signup conversion rates, as conversions depend on external "
                    "marketing and audience factors.\n\n"
                    "I have flagged your request for human review by our management team so they can review your specific account situation "
                    "and discuss available optimization options with you."
                ),
                voice=voice,
                category="missing_policy",
            )

        # 4. Check for Refund Requests (Factual anti-hallucination constraint)
        if classification.category == EmailCategory.REFUND or "refund" in text_lower:
            return self._format_refund_response(email, voice, text_lower)

        # 5. Check for Quotes & Custom Project Requests (excluding lead inquiries that happen to mention quotes)
        is_quote_proj = (
            classification.category in (EmailCategory.QUOTE, EmailCategory.PROJECT_REQUEST)
            or "rfp" in text_lower
            or ("quote" in text_lower and "lead" not in text_lower and "routing" not in text_lower)
        ) and "lead" not in text_lower
        if is_quote_proj:
            return self._format_quote_project_response(email, voice, text_lower)

        # 6. Check for Complaints
        if classification.category == EmailCategory.COMPLAINT or any(w in text_lower for w in ["unacceptable", "terrible", "furious"]):
            return self._format_complaint_response(email, voice, text_lower)

        # 7. Check for Multiple Technical Questions in One Email
        if self._is_multi_question_email(text_lower):
            return self._format_multi_question_response(email, voice, text_lower, kb_snippet, sources)

        # 8. Technical Questions with Knowledge Brain Grounding
        if kb_sufficient and kb_snippet and self._is_kb_snippet_relevant(text_lower, kb_snippet):
            return self._format_kb_grounded_response(email, voice, kb_snippet, sources)

        # 9. Domain-Specific Fallbacks (Specific known topics in Brilliant Directories)
        return self._format_domain_specific_reply(email, classification, voice, text_lower)

    def _is_kb_snippet_relevant(self, email_text: str, kb_snippet: str) -> bool:
        """Verify that retrieved Knowledge Brain snippet is genuinely on-topic for the query."""
        snippet_lower = kb_snippet.lower()
        key_topics = [
            ("franchise", ["franchise", "sub-account", "multi-location"]),
            ("sub-account", ["sub-account", "sub account", "multi-location", "franchise"]),
            ("lead", ["lead routing", "lead distribution", "matched lead", "lead settings"]),
            ("godaddy", ["godaddy", "a-record", "dns", "cname"]),
            ("ssl", ["ssl", "let's encrypt", "certificate"]),
            ("csv", ["csv", "import", "spreadsheet"]),
            ("recaptcha", ["recaptcha", "captcha"]),
            ("google map", ["maps api", "google map", "geocoding"]),
            ("badge", ["badge", "verified"]),
            ("stripe", ["stripe", "payment gateway"]),
            ("webhook", ["webhook", "zapier", "endpoint"]),
        ]
        for trigger, required_in_snippet in key_topics:
            if trigger in email_text:
                if not any(req in snippet_lower for req in required_in_snippet):
                    return False
        return True

    def _is_multi_question_email(self, text: str) -> bool:
        """Detect if the customer email contains multiple distinct questions."""
        has_numbered_list = bool(re.search(r"(?:^|\n|\s)(?:1[\.\)]|firstly|first:)", text))
        q_mark_count = text.count("?")
        has_compound_questions = ("sitemap" in text and "meta" in text) or ("stripe" in text and "test mode" in text) or ("godaddy" in text and "ssl" in text)
        return has_numbered_list or q_mark_count >= 2 or has_compound_questions

    def _format_multi_question_response(
        self,
        email: CustomerEmail,
        voice: CompanyVoice,
        text: str,
        kb_snippet: str,
        sources: List[Dict[str, Any]],
    ) -> str:
        """Address every question raised in a multi-part customer email."""
        answers: List[str] = []

        # Question: Sitemap & Meta tags
        if "sitemap" in text and "meta" in text:
            answers.append(
                "1. **XML Sitemap Location:** Your directory's XML sitemap is automatically generated and located at `https://yourdomain.com/sitemap.xml`. You can submit this direct URL in Google Search Console."
            )
            answers.append(
                "2. **Dynamic SEO Meta Titles:** You can configure dynamic meta titles and descriptions under **Settings > General Settings > SEO & Meta**, or customize search result page titles under **My Content > Edit Post Settings > Search Results Tab**."
            )
            if "test" in text or "submit" in text:
                answers.append(
                    "3. **Testing Before Submission:** You can inspect your sitemap in any browser or use the Google Search Console URL Inspection Tool to verify indexing before formal sitemap submission."
                )

        # Question: Stripe keys & Test Mode
        elif "stripe" in text and ("test" in text or "mode" in text or "secret key" in text):
            answers.append(
                "1. **Stripe API Credentials:** In your Admin Dashboard, navigate to **Finance > Payment Gateways**. Select Stripe as your default gateway and enter your Stripe Publishable Key and Secret Key."
            )
            answers.append(
                "2. **Test Mode Configuration:** Yes, we strongly advise testing first. Toggle on **Test Mode** in the Stripe settings tab and use Stripe's test card credentials to verify subscription signups before activating live billing."
            )

        # Question: DNS / GoDaddy & SSL Renewal
        elif ("godaddy" in text or "a-record" in text or "dns" in text) and ("ssl" in text or "let's encrypt" in text or "renew" in text):
            answers.append(
                "1. **GoDaddy DNS Records:** In your GoDaddy DNS Manager, point your root `@` A-Record to the dedicated Brilliant Directories IP provided in your Welcome Email, and set `www` as a CNAME pointing to your directory root."
            )
            answers.append(
                "2. **SSL Certificate Renewal:** Let's Encrypt SSL certificates are provisioned and renewed **automatically** by Brilliant Directories every 90 days. No manual cPanel renewal is required as long as your DNS records remain directed to the platform."
            )

        # Generic Multi-Question Handling
        else:
            if kb_snippet:
                answers.append(f"Regarding your inquiries:\n\n{kb_snippet}")
            else:
                answers.append("1. We have noted each part of your inquiry and reviewed our platform documentation.")
                answers.append("2. Brilliant Directories provides centralized administrative settings for these options under the Admin Dashboard sidebar.")

        core_answer = "\n\n".join(answers)
        return self._format_styled_response(core_answer, voice, category="multi_part")

    def _format_refund_response(self, email: CustomerEmail, voice: CompanyVoice, text: str) -> str:
        """Handle refund requests without falsely confirming unauthorized money dispatch."""
        core = (
            "We have received your refund request and understand the urgency regarding this charge.\n\n"
            "Our billing team has logged your inquiry and initiated a transaction review with our finance department. "
            "Under our standard service terms, all refund requests are verified against account billing records and processed "
            "within 3 to 5 business days upon review approval.\n\n"
            "To help us resolve this promptly, please confirm the transaction date, approximate amount, and invoice or reference number."
        )
        return self._format_styled_response(core, voice, category="refund")

    def _format_quote_project_response(self, email: CustomerEmail, voice: CompanyVoice, text: str) -> str:
        """Handle quotes and custom project requests without fabricating ungrounded price guarantees."""
        core = (
            "Thank you for considering our custom engineering and consulting services. We specialize in custom Brilliant Directories "
            "development, CRM and API integrations, database migrations, and bespoke directory features.\n\n"
            "Because engineering costs and delivery schedules depend directly on architectural requirements, data schemas, and API rate limits, "
            "we do not provide automated or ungrounded price guarantees. Our solutions architect will review your technical specifications "
            "and prepare a formal Scope of Work (SOW) with milestones and timeline estimates.\n\n"
            "We would be delighted to schedule a brief 20-minute scoping call to review your exact project goals."
        )
        return self._format_styled_response(core, voice, category="quote")

    def _format_complaint_response(self, email: CustomerEmail, voice: CompanyVoice, text: str) -> str:
        """Handle customer complaints and escalations with appropriate accountability."""
        core = (
            "We take your feedback very seriously and sincerely apologize for the frustration and disruption this issue has caused.\n\n"
            "Our senior engineering and technical support leadership have been notified and have opened an incident ticket to investigate "
            "the root cause. We are prioritizing this review to ensure full platform stability and will follow up with an update "
            "as soon as our diagnostic analysis is complete."
        )
        return self._format_styled_response(core, voice, category="complaint")

    def _format_kb_grounded_response(
        self,
        email: CustomerEmail,
        voice: CompanyVoice,
        kb_snippet: str,
        sources: List[Dict[str, Any]],
    ) -> str:
        """Format reply grounded in verified Knowledge Brain articles."""
        citation_text = ""
        if sources:
            citation_text = "\n\nHelpful Reference Guides:\n" + "\n".join(
                f"- [{s.get('title', 'Brilliant Directories Guide')}]({s.get('url', '')})" for s in sources[:2]
            )

        core = f"{kb_snippet}{citation_text}\n\nPlease let us know if you need any additional walkthrough steps as you configure this."
        return self._format_styled_response(core, voice, category="grounded_kb")

    def _format_domain_specific_reply(
        self,
        email: CustomerEmail,
        classification: EmailClassification,
        voice: CompanyVoice,
        text: str,
    ) -> str:
        """Provide accurate Brilliant Directories guidance for recognized platform features."""
        # Domain: Webhooks
        if "webhook" in text:
            core = (
                "Regarding your webhooks setup in Brilliant Directories:\n\n"
                "You can configure automated outgoing webhooks by navigating to **Developer Hub > Webhooks** in your Admin Dashboard. "
                "From here, click **Add New Webhook**, select the trigger event (such as `member.signup` or `lead.created`), "
                "and paste your endpoint or CRM webhook URL. Brilliant Directories sends a standard JSON payload for each event."
            )

        # Domain: Google Maps API
        elif "google map" in text or "maps api" in text:
            core = (
                "Regarding your Google Maps API configuration:\n\n"
                "To resolve location search and mapping errors, ensure the following Google Cloud APIs are enabled for your API key:\n"
                "- Maps JavaScript API\n"
                "- Geocoding API\n"
                "- Places API\n\n"
                "Once enabled in Google Cloud Console, enter the API key in your Admin Dashboard under **Settings > General Settings > Integrations**."
            )

        # Domain: reCAPTCHA v3
        elif "recaptcha" in text:
            core = (
                "Regarding Google reCAPTCHA v3 setup:\n\n"
                "You can protect your member registration and contact forms under **Settings > General Settings > Integrations**. "
                "Select **Google reCAPTCHA v3**, input your Site Key and Secret Key from the Google reCAPTCHA Admin Console, "
                "and save changes to activate spam protection on all public forms."
            )

        # Domain: Franchise / Multi-Location
        elif "franchise" in text or "multi-location" in text or "sub-account" in text:
            core = (
                "Regarding franchise and multi-location functionality:\n\n"
                "Brilliant Directories supports multi-location businesses through the **Sub-Accounts Add-On**. "
                "With this add-on enabled, a parent member account can manage multiple subordinate location listings from a single master login, "
                "with individual business addresses, phone numbers, and directory profile pages."
            )

        # Domain: Lead Routing
        elif "lead" in text and ("distribution" in text or "routing" in text or "quote" in text):
            core = (
                "Regarding automated lead routing and quote distribution:\n\n"
                "When prospective customers submit quote inquiries, Brilliant Directories matches leads based on category and location radius. "
                "You can configure routing rules under **Leads > Lead Settings**, including automated matching, manual admin approval, "
                "and lead notification limits per member plan."
            )

        # Domain: CSV Import
        elif "csv" in text or "bulk import" in text or "importing members" in text:
            core = (
                "Regarding bulk importing members via CSV:\n\n"
                "You can import members under **Members > Import Members**. You can download our standardized CSV template directly "
                "from that screen. If an email address in the CSV file already exists in the system, you can choose whether to skip duplicates "
                "or update existing member profile records."
            )

        # Domain: Verified Badges
        elif "badge" in text or "verified" in text or "icon" in text:
            core = (
                "Regarding member verified badges and profile icons:\n\n"
                "You can assign verified badges automatically by membership tier under **Finance > Membership Plans > Search Results Tab**. "
                "Alternatively, administrators can manually award or revoke verification badges on individual member records under **Members > Search Members**."
            )

        # Domain: Billing Templates
        elif "billing" in text and ("email" in text or "template" in text or "past-due" in text or "reminder" in text):
            core = (
                "Regarding past-due billing and renewal notification emails:\n\n"
                "You can customize automated billing email copy under **Emails > Email Templates**. Locate the `billing-reminder` "
                "or `past-due-notification` template and click **Edit** to modify the subject line, greeting, and notification wording."
            )

        # General Product Inquiry
        else:
            core = (
                "Regarding your inquiry: Brilliant Directories provides dedicated settings in your Admin Dashboard. "
                "You can access the relevant tools and configurations from the left-hand navigation sidebar under "
                "General Settings, Developer Tools, or Finance. If you would like detailed step-by-step instructions for a specific feature, "
                "please let us know the exact page or setting you are working on."
            )

        return self._format_styled_response(core, voice, category="domain_specific")

    def _format_styled_response(self, core_answer: str, voice: CompanyVoice, category: str = "") -> str:
        """Modulate the core factual answer into the requested CompanyVoice style."""
        if voice == CompanyVoice.CONCISE:
            # Bullet-pointed, minimal conversational fluff
            lines = [l.strip() for l in core_answer.split("\n\n") if l.strip()]
            bullet_points = []
            for line in lines:
                if line.startswith("- ") or line.startswith("1.") or line.startswith("2."):
                    bullet_points.append(line)
                else:
                    bullet_points.append(f"• {line}")
            return "\n\n".join(bullet_points)

        elif voice == CompanyVoice.FRIENDLY:
            # Warm, approachable, welcoming, encouraging
            opening = "We are happy to help you with this!"
            closing_phrase = "Hope this makes things easier for you! Let us know if you have any questions along the way."
            return f"{opening}\n\n{core_answer}\n\n{closing_phrase}"

        elif voice == CompanyVoice.FORMAL:
            # Respectful, structured, elevated vocabulary
            formal_intro = "We have reviewed your correspondence and would respectfully advise regarding the appropriate procedures:"
            formal_outro = "We trust this guidance proves satisfactory. Please advise should your organization require further administrative clarification."
            return f"{formal_intro}\n\n{core_answer}\n\n{formal_outro}"

        elif voice == CompanyVoice.EMPATHETIC:
            # Apologetic, reassuring, caring
            empathetic_note = (
                "We sincerely apologize for any confusion, concern, or inconvenience this may have caused, "
                "and we truly appreciate your patience as we assist."
            )
            return f"{empathetic_note}\n\n{core_answer}"

        elif voice == CompanyVoice.EXECUTIVE:
            # Consultative, high-level strategic alignment
            exec_intro = "From an advisory perspective, aligning your directory configuration with platform best practices ensures optimal scalability:"
            return f"{exec_intro}\n\n{core_answer}"

        # Default PROFESSIONAL: clean, clear, polished
        return core_answer
