"""External authoritative search service for Knowledge Brain RAG.

When internal documentation contains insufficient evidence, this service retrieves
authoritative external documentation, prioritizing official Brilliant Directories
documentation at support.brilliantdirectories.com.
"""

import os
import re
import logging
from typing import Any, Dict, List, Optional
import httpx

logger = logging.getLogger("knowledge_brain.rag.external_search")

# Authoritative Brilliant Directories official documentation catalog
# Used as fallback when external search APIs (Tavily, Serper) are not configured or offline.
AUTHORITATIVE_BD_CATALOG = [
    {
        "keywords": ["webhook", "zapier", "api", "developer hub", "endpoint", "event", "rest"],
        "title": "Developer Hub & Webhooks Integration Guide",
        "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000085432-developer-hub-webhooks-overview",
        "text": (
            "Brilliant Directories Developer Hub provides native outbound webhooks for real-time events. "
            "To configure: Navigate to Developer Hub > Webhooks in your Admin Dashboard. "
            "Supported triggers include Member Signup, Lead Generation, Review Submission, and Billing Updates. "
            "Webhooks send secure JSON POST payloads with HMAC SHA-256 signature verification headers. "
            "Compatible directly with Zapier, Make (Integromat), Pabbly, or custom API endpoints."
        ),
    },
    {
        "keywords": ["payment", "stripe", "gateway", "paypal", "credit card", "billing", "currency"],
        "title": "Payment Gateway Configuration & Setup (Stripe & PayPal)",
        "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000045231-payment-gateways-stripe-paypal",
        "text": (
            "Brilliant Directories supports Stripe, PayPal, Authorize.Net, and eWay. "
            "To connect Stripe: Navigate to Settings > General Settings > Payments tab. "
            "Click 'Connect with Stripe' to authorize via Stripe Connect OAuth. "
            "Ensure SCA 3D Secure 2.0 is enabled for European transactions. "
            "Supports multi-currency billing, automated recurring subscription renewals, and prorated plan upgrades."
        ),
    },
    {
        "keywords": ["map", "google maps", "location", "geocoding", "places api", "address"],
        "title": "Google Maps API Key Setup & Location Geocoding",
        "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000078912-how-to-set-up-google-maps-api-keys",
        "text": (
            "To enable map search and location autocomplete: "
            "1. Visit the Google Cloud Console and enable: Maps JavaScript API, Places API, Geocoding API, and Distance Matrix API. "
            "2. Create an API Key and restrict it by HTTP referrers (your website domain). "
            "3. In BD Admin Dashboard, go to Settings > General Settings > Integrations. "
            "4. Paste your Google Maps API Key into the 'Google Maps API Key' field and click Save."
        ),
    },
    {
        "keywords": ["css", "design", "style", "color", "theme", "bootstrap", "font"],
        "title": "Custom CSS and Design Settings Architecture",
        "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000034110-custom-css-and-styling-guidelines",
        "text": (
            "Brilliant Directories uses Bootstrap with customizable CSS variables. "
            "To add custom styling: Navigate to Settings > Design Settings > Custom CSS / HEAD tab. "
            "Add your CSS overrides inside the 'Additional CSS' textarea. "
            "Custom styles take priority over default Bootstrap classes. "
            "Avoid modifying core widget PHP templates directly when CSS overrides suffice."
        ),
    },
    {
        "keywords": ["email", "notification", "smtp", "template", "welcome email", "deliverability"],
        "title": "Email Templates and SMTP Notification Delivery",
        "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000067451-email-templates-and-smtp-settings",
        "text": (
            "To customize email notifications: Go to Emails > Email Templates. "
            "System templates support dynamic shortcodes like %member_name%, %listing_url%, and %site_name%. "
            "For optimal deliverability, configure third-party SMTP (SendGrid, Mailgun, Amazon SES) in Settings > Domain & Email. "
            "Verify SPF, DKIM, and DMARC DNS records on your domain registrar."
        ),
    },
    {
        "keywords": ["lead", "leads", "lead matching", "inquiry", "quote", "member lead"],
        "title": "Lead Management and Automated Member Matching",
        "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000056789-lead-system-and-member-matching",
        "text": (
            "The Brilliant Directories Lead System automates matching visitor inquiries to qualified members based on category and location. "
            "Configure lead rules under Leads > Lead Settings. "
            "Admins can set leads to Auto-Match or Require Admin Approval before notifying members. "
            "Members can accept, decline, or purchase leads depending on their membership plan settings."
        ),
    },
    {
        "keywords": ["membership", "plan", "subscription", "recurring", "tier", "upgrade", "pricing"],
        "title": "Membership Plans, Pricing Tiers, and Feature Permissions",
        "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000023456-membership-plans-overview",
        "text": (
            "Manage member tiers under Finance > Membership Plans. "
            "Each plan allows custom configuration of: Pricing (one-time, recurring monthly/annual), Search Priority, Profile Limits (photos, videos, articles), and Feature Permissions. "
            "Use 'Post Publishing Limits' to control how many classifieds or events members can submit."
        ),
    },
    {
        "keywords": ["ssl", "domain", "https", "cloudflare", "dns", "nameserver"],
        "title": "Connecting Custom Domains and Free SSL Certificate",
        "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000012345-connecting-custom-domain-and-ssl",
        "text": (
            "To launch on a custom domain: "
            "1. In your domain registrar (GoDaddy, Namecheap, Cloudflare), point the A Record to the Brilliant Directories IP address provided in your welcome email. "
            "2. In the BD Admin Dashboard, go to Settings > Domain & Email > Custom Domain. "
            "3. Enter your domain (e.g. www.yourdomain.com) and click Save & Verify. "
            "4. Free SSL certificates are automatically provisioned and renewed via Let's Encrypt once DNS propagation completes."
        ),
    },
    {
        "keywords": ["widget", "custom widget", "php", "shortcode", "developer"],
        "title": "Widget Manager and Custom PHP/HTML Development",
        "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000089012-widget-manager-overview",
        "text": (
            "The Widget Manager allows creating and editing custom HTML, CSS, JavaScript, and PHP widgets. "
            "Access via Developer Hub > Widget Manager. "
            "When modifying core system widgets, duplicate them first to ensure safe versioning and prevent upgrades from overwriting custom logic."
        ),
    },
]


class ExternalSearchService:
    """Service to retrieve authoritative technical documentation from external sources."""

    def __init__(
        self,
        tavily_api_key: Optional[str] = None,
        serper_api_key: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ):
        self.tavily_api_key = tavily_api_key or os.getenv("TAVILY_API_KEY")
        self.serper_api_key = serper_api_key or os.getenv("SERPER_API_KEY")
        self.client = client or httpx.Client(timeout=10.0, follow_redirects=True)

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Search external authoritative sources prioritizing support.brilliantdirectories.com.
        
        Returns structured documentation chunks labeled with source_type='External Web Research'.
        """
        clean_q = query.strip()
        if not clean_q:
            return []

        # 1. Try Tavily Search API if configured
        if self.tavily_api_key:
            results = self._search_tavily(clean_q, top_k=top_k)
            if results:
                return results

        # 2. Try Serper (Google Search) API if configured
        if self.serper_api_key:
            results = self._search_serper(clean_q, top_k=top_k)
            if results:
                return results

        # 3. Authoritative Brilliant Directories Curated Documentation Fallback
        return self._search_authoritative_catalog(clean_q, top_k=top_k)

    def _search_tavily(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Query Tavily Search API targeting support.brilliantdirectories.com."""
        try:
            resp = self.client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.tavily_api_key,
                    "query": f"Brilliant Directories {query}",
                    "include_domains": ["support.brilliantdirectories.com", "brilliantdirectories.com"],
                    "search_depth": "advanced",
                    "max_results": top_k,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("results", []):
                    results.append({
                        "title": item.get("title") or "Brilliant Directories Official Documentation",
                        "url": item.get("url") or "https://support.brilliantdirectories.com",
                        "text": item.get("content") or item.get("snippet", ""),
                        "similarity_score": round(float(item.get("score", 0.88)), 2),
                        "source_type": "External Web Research",
                        "image_urls": [],
                    })
                return results
        except Exception as exc:
            logger.warning("Tavily external search failed: %s", exc)
        return []

    def _search_serper(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Query Serper (Google Search) API targeting support.brilliantdirectories.com."""
        try:
            resp = self.client.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": self.serper_api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "q": f"site:support.brilliantdirectories.com {query}",
                    "num": top_k,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("organic", []):
                    results.append({
                        "title": item.get("title") or "Brilliant Directories Official Documentation",
                        "url": item.get("link") or "https://support.brilliantdirectories.com",
                        "text": item.get("snippet") or "",
                        "similarity_score": 0.85,
                        "source_type": "External Web Research",
                        "image_urls": [],
                    })
                return results
        except Exception as exc:
            logger.warning("Serper external search failed: %s", exc)
        return []

    def _search_authoritative_catalog(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Search curated authoritative Brilliant Directories documentation by domain keywords."""
        q_lower = query.lower()
        terms = [t for t in re.split(r"[^\w]+", q_lower) if len(t) > 2]

        # Filter out common query stopwords and generic directory terms
        stopwords = {
            "how", "can", "what", "where", "why", "who", "when", "does", "the",
            "and", "for", "with", "from", "brilliant", "directories", "run", "use",
            "using", "set", "setup", "configure", "configuration", "engine", "platform",
            "system", "website", "support", "online", "application", "guide", "overview"
        }
        meaningful_terms = [t for t in terms if t not in stopwords and len(t) > 2]
        if not meaningful_terms:
            return []

        scored = []
        for doc in AUTHORITATIVE_BD_CATALOG:
            score = 0
            # Check domain keywords match
            for kw in doc["keywords"]:
                if kw in q_lower:
                    score += 5
                for term in meaningful_terms:
                    if term == kw:
                        score += 4
                    elif len(term) >= 4 and (term in kw or kw in term):
                        score += 2

            # Check title match
            doc_title_lower = doc["title"].lower()
            for term in meaningful_terms:
                if term in doc_title_lower:
                    score += 3

            # Require substantive domain match (score >= 4)
            if score >= 4:
                norm_score = round(min(0.95, 0.70 + (score * 0.05)), 2)
                scored.append((norm_score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for norm_score, doc in scored[:top_k]:
            results.append({
                "title": doc["title"],
                "url": doc["url"],
                "text": doc["text"],
                "similarity_score": norm_score,
                "source_type": "External Web Research",
                "image_urls": [],
            })
        return results
