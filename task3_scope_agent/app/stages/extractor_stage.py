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
    """Parses raw meeting transcripts to extract structured requirements, assumptions, constraints, and open questions."""

    VAGUE_PHRASES = [
        "some sort of", "as soon as possible", "make it look nice", "standard way",
        "simple solution", "quick turnaround", "modern look", "modern feel", "flexible", "etc"
    ]

    CONVERSATIONAL_GREETINGS = [
        "welcome", "hello", "hi ", "good morning", "good afternoon", "let's discuss", "thank you for joining"
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
        lines = []
        for rl in raw_lines:
            parts = re.split(r";\s*|\s+and\s+(?=a\s+custom\b|we\s+(?:also\s+)?(?:need|want)\b)", rl, flags=re.IGNORECASE)
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
            lower = line.lower()
            # Clean speaker prefix (e.g., "John (Client): We need...")
            cleaned = re.sub(r"^[^:]+:\s*", "", line).strip()
            cleaned_lower = cleaned.lower()

            # Skip conversational introductions
            if any(cleaned_lower.startswith(g) for g in self.CONVERSATIONAL_GREETINGS):
                continue

            # Constraint extraction
            if any(k in cleaned_lower for k in ["deadline", "launch by", "budget", "must not", "limited to", "within 4 weeks"]):
                constraints.append(cleaned)

            # Dependency extraction
            if any(k in cleaned_lower for k in ["need access to", "depends on", "provided by client", "api credentials", "figma"]):
                dependencies.append(cleaned)

            # Assumption extraction
            if any(k in cleaned_lower for k in ["assuming", "we assume", "we expect", "probably", "presumably"]):
                assumptions.append(cleaned)

            # Requirement detection
            req_triggers = [
                "need", "want", "require", "feature", "integrate", "must have",
                "should", "build", "support", "make sure", "in addition", "custom", "portal"
            ]
            if any(k in cleaned_lower for k in req_triggers):
                title, desc = self._parse_requirement_text(line)
                is_vague, question = self._check_ambiguity(cleaned)

                # Determine type
                req_type = RequirementType.FUNCTIONAL
                if any(w in cleaned_lower for w in ["sync", "api", "webhook", "salesforce", "stripe", "zapier", "mobile app"]):
                    req_type = RequirementType.INTEGRATION
                elif any(w in cleaned_lower for w in ["speed", "latency", "scale", "performance"]):
                    req_type = RequirementType.NON_FUNCTIONAL

                req_item = RequirementItem(
                    id=f"REQ-{req_counter:03d}",
                    title=title,
                    description=desc,
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
                    is_ambiguous=True,
                    clarification_question="Could you provide specific user stories or workflows for this engagement?",
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

    def _parse_requirement_text(self, line: str) -> Tuple[str, str]:
        # Strip speaker label e.g. "John (Client): We need..."
        cleaned = re.sub(r"^[^:]+:\s*", "", line).strip()
        words = cleaned.split()
        title = " ".join(words[:8]).rstrip(":,.")
        return title, cleaned

    def _check_ambiguity(self, text: str) -> Tuple[bool, Optional[str]]:
        lower = text.lower()
        for phrase in self.VAGUE_PHRASES:
            if phrase in lower:
                return (
                    True,
                    f"The requirement mentions '{phrase}'. What are the exact acceptance criteria and design specifications?",
                )
        if len(text.split()) < 4:
            return True, "This requirement is brief. What specific user actions and endpoints are expected?"
        return False, None
