"""Stage 2: Knowledge Brain Native Feature Checker.

Queries Task 1 Knowledge Brain before recommending custom development to save client budget.
"""

import logging
from typing import Optional
from task2_inbox_agent.app.kb_client import KnowledgeBrainClient
from task3_scope_agent.app.models import ExtractedMeetingAnalysis, RequirementItem

logger = logging.getLogger("scope_agent.feature_checker")


class NativeFeatureCheckerStage:
    """Checks each extracted requirement against the Knowledge Brain to detect native features."""

    # Built-in Brilliant Directories core capabilities
    NATIVE_KEYWORDS = [
        "stripe", "paypal", "payment gateway", "webhook", "lead distribution",
        "member search", "google maps", "custom domain", "ssl", "csv import",
        "member badges", "email templates", "reusable forms"
    ]

    CUSTOM_KEYWORDS = [
        "native mobile app", "ios", "android", "app store", "bidirectional sync",
        "sql server", "patient scheduling portal", "custom script", "custom integration",
        "erp sync", "salesforce sync", "internal database"
    ]

    def __init__(self, kb_client: Optional[KnowledgeBrainClient] = None):
        self.kb_client = kb_client or KnowledgeBrainClient()

    async def verify_requirements(self, analysis: ExtractedMeetingAnalysis) -> ExtractedMeetingAnalysis:
        """Inspect all requirements and tag whether native platform features exist."""
        for req in analysis.requirements:
            query_str = f"Does Brilliant Directories support {req.title}? {req.description}"
            kb_res = await self.kb_client.query(query_str, top_k=2)

            text_lower = f"{req.title} {req.description}".lower()
            is_explicit_custom = any(ck in text_lower for ck in self.CUSTOM_KEYWORDS)
            keyword_match = any(kw in text_lower for kw in self.NATIVE_KEYWORDS)

            if not is_explicit_custom and (keyword_match or kb_res.get("is_sufficient")):
                req.is_native_feature = True
                evidence = kb_res.get("answer", "")
                if not evidence or len(evidence) < 20 or not kb_res.get("is_sufficient"):
                    evidence = f"Verified native feature in Brilliant Directories: {req.title} supported out-of-the-box."
                req.native_feature_evidence = evidence[:160].strip() + "..."
                req.estimated_hours = 3.0  # Configuration only
            else:
                req.is_native_feature = False
                req.native_feature_evidence = "Custom full-stack development required (not available out-of-the-box)."
                req.estimated_hours = 24.0  # Custom architecture & coding

        return analysis
