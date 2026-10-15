"""Email Intent and Priority Classifier with Validated Pydantic Schemas."""

import re
from typing import List, Tuple
from task2_inbox_agent.app.models import (
    CustomerEmail,
    EmailCategory,
    EmailClassification,
    EmailPriority,
)


class EmailClassifier:
    """Classifies incoming customer emails into 6 distinct categories with priority scoring."""

    # Lexical triggers with category association
    KEYWORD_MAPPINGS = {
        EmailCategory.REFUND: [
            "refund", "double charge", "charged twice", "cancel", "money back",
            "overcharged", "billing dispute", "accidental renewal", "reverse charge"
        ],
        EmailCategory.COMPLAINT: [
            "unacceptable", "broken", "appalling", "terrible", "outage", "bug",
            "spam", "error 502", "slow", "failing", "not working", "corrupt", "freeze"
        ],
        EmailCategory.QUOTE: [
            "quote", "rfp", "pricing options", "estimate", "how much would it cost",
            "pricing quote", "consulting fee", "proposal", "volume discount"
        ],
        EmailCategory.PROJECT_REQUEST: [
            "custom development", "redesign", "integration project", "custom theme",
            "migration project", "custom widget", "custom api", "engineering engagement", "scope of work"
        ],
        EmailCategory.FOLLOW_UP: [
            "following up", "checking in", "status update", "ticket #", "checking status",
            "any update", "did you have a chance", "has this been authorized"
        ],
        EmailCategory.PRODUCT_QUESTION: [
            "how do i", "how does", "where can i", "where in the admin", "configure",
            "setup", "credentials", "documentation", "settings", "sitemap", "api key", "recaptcha"
        ],
    }

    def classify(self, email: CustomerEmail) -> EmailClassification:
        """Classify customer email using deterministic rule scoring and entity extraction."""
        text = f"{email.subject} {email.body}".lower()

        scores = {cat: 0 for cat in EmailCategory}
        for cat, keywords in self.KEYWORD_MAPPINGS.items():
            for kw in keywords:
                if kw in text:
                    # Subject matches carry higher weight
                    weight = 2 if kw in email.subject.lower() else 1
                    scores[cat] += weight

        # Determine winner
        best_category = max(scores, key=scores.get)
        max_score = scores[best_category]

        if max_score == 0:
            # Fallback based on question marks or standard inquiry
            best_category = EmailCategory.PRODUCT_QUESTION
            confidence = 0.65
            reasoning = "General inquiry detected; defaulting to Product Question."
        else:
            confidence = min(0.98, round(0.70 + (max_score * 0.06), 2))
            reasoning = f"Matched {max_score} category-specific indicators for {best_category.value}."

        # Priority calculation
        priority = self._calculate_priority(text, best_category)

        # Entity extraction
        entities = self._extract_entities(text)

        # Requires Knowledge Brain lookup if it's a product question or technical request
        requires_kb = best_category in (EmailCategory.PRODUCT_QUESTION, EmailCategory.PROJECT_REQUEST)

        return EmailClassification(
            category=best_category,
            priority=priority,
            confidence=confidence,
            reasoning=reasoning,
            requires_kb_lookup=requires_kb,
            key_entities=entities,
        )

    def _calculate_priority(self, text: str, category: EmailCategory) -> EmailPriority:
        if any(w in text for w in ["urgent", "immediately", "outage", "emergency", "costing us"]):
            return EmailPriority.URGENT
        if category in (EmailCategory.REFUND, EmailCategory.COMPLAINT) or any(w in text for w in ["high", "asap", "error", "broken"]):
            return EmailPriority.HIGH
        if category in (EmailCategory.QUOTE, EmailCategory.PROJECT_REQUEST):
            return EmailPriority.MEDIUM
        return EmailPriority.LOW

    def _extract_entities(self, text: str) -> List[str]:
        entities = []
        candidates = ["stripe", "paypal", "webhooks", "google maps", "recaptcha", "cloudflare", "dns", "ssl", "csv", "salesforce"]
        for c in candidates:
            if c in text:
                entities.append(c.title())
        return entities
