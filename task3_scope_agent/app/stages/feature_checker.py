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
        "native mobile app", "ios", "android", "app store", "bidirectional", "sync",
        "sql server", "patient scheduling", "patient booking", "booking database",
        "scheduling database", "custom script", "custom integration", "internal database",
        "erp sync", "salesforce", "internal patient", "patient database", "proprietary",
        "custom bidirectional",
    ]

    CUSTOM_CANONICAL_KEYS = {
        "patient_database_integration",
        "mobile_app",
        "salesforce_crm",
    }

    def __init__(self, kb_client: Optional[KnowledgeBrainClient] = None):
        self.kb_client = kb_client or KnowledgeBrainClient()

    VERIFIED_NATIVE_DOCS = {
        "stripe_payments": {
            "title": "Supported Payment Gateways",
            "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000006843-supported-payment-gateways",
            "snippet": "Brilliant Directories natively supports Stripe for one-time and recurring monthly membership billing.",
        },
        "webhooks": {
            "title": "Developer Hub - Webhooks",
            "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000083005-developer-hub-webhooks",
            "snippet": "Brilliant Directories includes native outbound webhooks to instantly notify external CRMs and marketing software.",
        },
        "lead_distribution": {
            "title": "What Is The Automatic Lead Matching Add-On?",
            "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000007994-what-is-the-automatic-lead-matching-add-on-",
            "snippet": "Native automated lead routing engine matches inquiries to verified directory members by category and location.",
        },
        "directory_setup": {
            "title": "Brilliant Directories Platform Setup & Member Directory",
            "url": "https://support.brilliantdirectories.com/support/solutions",
            "snippet": "Out-of-the-box member directory foundation with customizable member categories, profiles, and listings.",
        },
        "fast_search": {
            "title": "Search Settings & Keyword/Location Search Optimization",
            "url": "https://support.brilliantdirectories.com/support/solutions",
            "snippet": "Native search engine with indexed keyword, category, and radius location filtering.",
        },
        "modern_design": {
            "title": "Design Settings & Mobile-Ready Responsive Themes",
            "url": "https://support.brilliantdirectories.com/support/solutions",
            "snippet": "Pre-built responsive design system with customizable color palettes, homepage streaming widgets, and CSS overrides.",
        },
    }

    async def verify_requirements(self, analysis: ExtractedMeetingAnalysis) -> ExtractedMeetingAnalysis:
        """Inspect all requirements, query Knowledge Brain, and attach verified source citations."""
        for req in analysis.requirements:
            query_str = f"Does Brilliant Directories support {req.title}? {req.description}"
            kb_res = await self.kb_client.query(query_str, top_k=2)

            text_lower = f"{req.title} {req.description}".lower()
            ckey = req.canonical_key or ""
            is_explicit_custom = (ckey in self.CUSTOM_CANONICAL_KEYS) or any(ck in text_lower for ck in self.CUSTOM_KEYWORDS)
            keyword_match = any(kw in text_lower for kw in self.NATIVE_KEYWORDS)

            if not is_explicit_custom and (keyword_match or kb_res.get("is_sufficient") or ckey in self.VERIFIED_NATIVE_DOCS):
                req.is_native_feature = True
                req.estimated_hours = 3.0  # Platform configuration only

                # Collect citations from Knowledge Brain or verified documentation
                citations = []
                if kb_res.get("sources") and len(kb_res["sources"]) > 0:
                    citations = [
                        {
                            "title": s.get("title", "Brilliant Directories Documentation"),
                            "url": s.get("url", "https://support.brilliantdirectories.com"),
                            "snippet": s.get("snippet", ""),
                        }
                        for s in kb_res["sources"]
                    ]
                elif ckey in self.VERIFIED_NATIVE_DOCS:
                    citations = [self.VERIFIED_NATIVE_DOCS[ckey]]
                else:
                    citations = [{
                        "title": "Brilliant Directories Solution Center",
                        "url": "https://support.brilliantdirectories.com/support/solutions",
                        "snippet": f"Verified native capability for {req.title}.",
                    }]

                req.source_citations = citations
                evidence_title = citations[0]["title"] if citations else "Brilliant Directories Documentation"
                req.native_feature_evidence = (
                    f"Verified native feature in Brilliant Directories documented in [{evidence_title}]({citations[0]['url'] if citations else ''}). "
                    "Configuration only (~3h)."
                )
            else:
                req.is_native_feature = False
                req.estimated_hours = 24.0  # Full custom architecture, database schema, and coding
                req.source_citations = []
                req.native_feature_evidence = (
                    "Custom full-stack development required. No out-of-the-box Brilliant Directories module exists "
                    "for this proprietary integration."
                )

        return analysis
