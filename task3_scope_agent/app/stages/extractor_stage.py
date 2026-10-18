"""Stage 1: Transcript Information Extractor with Ambiguity Detection."""

import re
import uuid
from typing import List, Optional, Tuple
from task3_scope_agent.app.models import (
    ExtractedMeetingAnalysis,
    RequirementItem,
    RequirementType,
)


class TranscriptExtractorStage:
    """Parses raw meeting transcripts into actionable work-package requirements with ambiguity detection."""

    VAGUE_PHRASES = [
        "some sort of", "sort of", "kind of", "make it look nice", "standard way",
        "simple solution", "as soon as possible", "asap", "quick turnaround",
        "modern look", "modern feel", "modern design", "flexible", "etc"
    ]

    CONVERSATIONAL_GREETINGS = [
        "welcome", "hello", "hi ", "good morning", "good afternoon",
        "let's discuss", "thank you for joining", "welcome back", "what updates do we have"
    ]

    def extract(
        self,
        transcript_text: str,
        client_name: str = "Client",
        meeting_id: Optional[str] = None,
        version: int = 1,
    ) -> ExtractedMeetingAnalysis:
        m_id = meeting_id or f"MTG-{uuid.uuid4().hex[:6].upper()}"

        raw_lines = [line.strip() for line in transcript_text.splitlines() if line.strip()]
        lines: List[str] = []

        for rl in raw_lines:
            # First split sentences by period followed by space and capital letter or speaker
            sentences = re.split(r"\.\s+(?=[A-Z]|John|Sarah|Client|Consultant|\w+:)", rl)
            for s in sentences:
                if not s.strip():
                    continue
                # Split compound clauses containing distinct requirements
                parts = re.split(
                    r";\s*|,\s*(?=(?:we\s+)?(?:now\s+|also\s+|still\s+)?(?:need|want|require)\b)|\s+and\s+(?=(?:webhooks?|has|have|with|make sure|we\s+(?:also\s+|still\s+)?(?:need|want|require)\b|a\s+custom\b|custom\b|some\s+sort\b))",
                    s.strip(),
                    flags=re.IGNORECASE,
                )
                for p in parts:
                    if p.strip():
                        lines.append(p.strip())

        requirements: List[RequirementItem] = []
        assumptions: List[str] = []
        constraints: List[str] = []
        dependencies: List[str] = []
        open_questions: List[str] = []

        req_counter = 1

        for line in lines:
            # Clean speaker prefix (e.g., "John (Client): We need...")
            cleaned = re.sub(r"^[^:]+:\s*", "", line).strip()
            cleaned_lower = cleaned.lower()

            # Skip conversational introductions
            if any(cleaned_lower.startswith(g) for g in self.CONVERSATIONAL_GREETINGS):
                continue

            # Skip explicit drop / removal statements so they are not treated as active requirements
            if any(k in cleaned_lower for k in ["dropped", "drop ", "drop the", "no longer need", "remove ", "don't need", "do not need", "decided against"]):
                continue

            # Constraint extraction (Budget & Deadline)
            if any(k in cleaned_lower for k in ["deadline", "launch by", "budget", "must not", "limited to", "within 4 weeks", "launch within"]):
                constraints.append(cleaned)
                # If the line only contains constraints/timeline, don't treat as functional req
                if not any(k in cleaned_lower for k in ["stripe", "database", "portal", "search", "app", "sync"]):
                    continue

            # Dependency extraction
            if any(k in cleaned_lower for k in ["need access to", "depends on", "provided by client", "api credentials", "figma"]):
                dependencies.append(cleaned)

            # Assumption extraction
            if any(k in cleaned_lower for k in ["assuming", "we assume", "we expect", "probably", "presumably", "handles the underlying"]):
                assumptions.append(cleaned)

            # Requirement detection triggers
            req_triggers = [
                "need", "want", "require", "feature", "integrate", "must have",
                "should", "build", "support", "make sure", "in addition", "custom", "portal",
                "search", "stripe", "webhook", "directory", "filter", "mobile app", "billing",
                "design", "modern", "look", "feel", "ui", "layout", "database", "some sort",
                "sort of", "sync", "payment", "booking"
            ]
            if any(k in cleaned_lower for k in req_triggers):
                title, canonical_key, desc = self._generate_work_package_title(cleaned)
                is_vague, question = self._check_ambiguity(cleaned)

                # Check for existing requirement with same canonical key (prevent duplicate tasks)
                existing = next((r for r in requirements if r.canonical_key == canonical_key), None)
                if existing:
                    existing.description += f" / Additional context: {desc}"
                    if is_vague and not existing.is_ambiguous:
                        existing.is_ambiguous = True
                        existing.clarification_question = question
                    continue

                # Determine requirement type
                req_type = RequirementType.FUNCTIONAL
                if any(w in cleaned_lower for w in ["sync", "api", "webhook", "salesforce", "stripe", "zapier", "mobile app", "database"]):
                    req_type = RequirementType.INTEGRATION
                elif any(w in cleaned_lower for w in ["speed", "latency", "scale", "performance", "fast"]):
                    req_type = RequirementType.NON_FUNCTIONAL

                req_item = RequirementItem(
                    id=f"REQ-{req_counter:03d}",
                    title=title,
                    description=desc,
                    canonical_key=canonical_key,
                    req_type=req_type,
                    is_ambiguous=is_vague,
                    clarification_question=question,
                )
                requirements.append(req_item)
                if is_vague and question:
                    open_questions.append(f"Regarding '{title}': {question}")
                req_counter += 1

        # Fallback defaults if transcript was conversational or unstructured
        if not requirements:
            requirements.append(
                RequirementItem(
                    id="REQ-001",
                    title="Platform Consultation & Scoping",
                    description=transcript_text[:120].strip() + "...",
                    canonical_key="consultation_scoping",
                    is_ambiguous=True,
                    clarification_question="Could you provide specific user stories or workflow acceptance criteria for this engagement?",
                )
            )

        if not assumptions:
            assumptions.append("Client will supply required branding assets, domain access, and third-party credentials.")
        if not constraints:
            constraints.append("Implementation relies on standard Brilliant Directories platform capabilities where feasible.")

        return ExtractedMeetingAnalysis(
            meeting_id=m_id,
            version=version,
            client_name=client_name,
            project_title=f"{client_name} Directory Implementation (v{version})",
            requirements=requirements,
            assumptions=assumptions,
            constraints=constraints,
            dependencies=dependencies,
            open_questions=open_questions,
        )

    def _generate_work_package_title(self, text: str) -> Tuple[str, str, str]:
        """Convert raw transcript sentences into concise, actionable work-package titles."""
        cleaned = re.sub(r"^[^:]+:\s*", "", text).strip().rstrip(".,;")
        lower = cleaned.lower()

        # Domain-specific canonical matching for HealthHub & common directory requirements
        if "therapist" in lower or ("directory" in lower and any(w in lower for w in ["building", "setup", "create", "launch"])):
            if "therapist" in lower:
                return "Directory Setup for Certified Physical Therapists", "directory_setup", cleaned
            return "Member Directory Platform Setup", "directory_setup", cleaned

        if "stripe" in lower or ("membership" in lower and "payment" in lower) or ("pay" in lower and "monthly" in lower):
            if "monthly" in lower or "recurring" in lower or "membership" in lower:
                return "Stripe & Monthly Membership Payments Configuration", "stripe_payments", cleaned
            return "Stripe Payment Gateway Integration", "stripe_payments", cleaned

        if "webhook" in lower:
            if "marketing" in lower or "crm" in lower:
                return "Webhooks for Marketing Software Notifications", "webhooks", cleaned
            return "Webhook Event Notification Setup", "webhooks", cleaned

        if any(w in lower for w in ["patient scheduling", "booking database", "patient booking", "internal database", "sql server"]):
            return "Bidirectional Patient Scheduling Database Integration", "patient_database_integration", cleaned

        if ("search" in lower and any(w in lower for w in ["fast", "speed", "really fast", "quick"])) or "fast search" in lower:
            return "High-Performance Directory Search Optimization", "fast_search", cleaned

        if any(w in lower for w in ["modern design", "modern look", "modern feel", "design", "layout"]):
            return "Modern Responsive UI & Layout Design", "modern_design", cleaned

        if any(w in lower for w in ["mobile app", "ios", "android", "app store"]):
            return "Custom Native Mobile App (iOS & Android)", "mobile_app", cleaned

        if "multi-currency" in lower or "currencies" in lower:
            return "Multi-Currency Dynamic Billing Configuration", "multicurrency_billing", cleaned

        if "salesforce" in lower:
            return "Salesforce CRM API Integration", "salesforce_crm", cleaned

        if "medical license" in lower or "license number" in lower:
            return "Medical License Number Directory Search Filter", "search_filter_medical", cleaned

        if "lead distribution" in lower or "lead matching" in lower:
            return "Automated Lead Distribution Engine Setup", "lead_distribution", cleaned

        # General algorithmic cleanup for arbitrary statements
        stripped = re.sub(
            r"^(?:also,?\s*|then\s*|in addition,?\s*|and\s+|we (?:are building|need to|also need to|also need|need|want to|want|require|must have|should have|would like)|make sure (?:that\s+)?|make sure the|yes,?\s*(?:we want members to|we need to)?|please\s+|can we\s+)",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()

        words = stripped.split()
        short_title = " ".join(words[:7]).rstrip(":,.") if len(words) > 7 else stripped.rstrip(":,.")
        title_words = [w.capitalize() if not w.isupper() else w for w in short_title.split()]
        actionable_title = " ".join(title_words)
        canonical_key = re.sub(r"[^a-z0-9]+", "_", actionable_title.lower()).strip("_")

        return actionable_title, canonical_key, cleaned

    def _check_ambiguity(self, text: str) -> Tuple[bool, Optional[str]]:
        lower = text.lower()
        if any(k in lower for k in ["modern design", "modern look", "modern feel", "some sort of modern", "look nice"]):
            return (
                True,
                "What specific reference websites, typography, or UI component criteria define your preferred modern aesthetic? Do you have Figma mockups or style guides?",
            )
        if any(k in lower for k in ["fast search", "really fast", "speed"]):
            return (
                True,
                "What is the expected member directory record volume, and what is the target query response time latency threshold (e.g. sub-500ms)? Are there specific criteria for keyword matching?",
            )
        for phrase in self.VAGUE_PHRASES:
            if phrase in lower:
                return (
                    True,
                    f"The requirement mentions '{phrase}'. What are the exact acceptance criteria, technical endpoints, and business rules?",
                )
        if len(text.split()) < 4:
            return True, "This requirement is brief. What specific user actions, acceptance criteria, and endpoints are expected?"
        return False, None
