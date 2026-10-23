"""AI Agent Self-Healing Module for HTTP 300/404 Crawl & Query Exceptions.

When a URL encounters an HTTP 300 (multiple choices/redirect issue) or 404 (Not Found),
this AI module triggers autonomous self-search across the Knowledge Brain's indexed
documents and relative solutions to resolve the broken reference without halting.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("knowledge_brain.self_healing")


class SelfHealingResult:
    """Structured report of an autonomous self-healing resolution."""

    def __init__(
        self,
        url: str,
        error_code: int,
        resolved: bool,
        resolved_url: Optional[str] = None,
        relative_docs: Optional[List[Dict[str, Any]]] = None,
        resolution_notes: str = "",
    ):
        self.url = url
        self.error_code = error_code
        self.resolved = resolved
        self.resolved_url = resolved_url
        self.relative_docs = relative_docs or []
        self.resolution_notes = resolution_notes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "error_code": self.error_code,
            "resolved": self.resolved,
            "resolved_url": self.resolved_url,
            "relative_docs": self.relative_docs,
            "resolution_notes": self.resolution_notes,
        }


class SelfHealingAgent:
    """Autonomous healing agent for 300/404 URL crashes."""

    def __init__(self, state_store=None, rag_engine=None):
        self.state_store = state_store
        self.rag_engine = rag_engine

    def extract_search_terms(self, url: str) -> str:
        """Extract clean semantic keywords from URL path and slug."""
        try:
            parsed = urlparse(url)
            path = parsed.path
            # Get trailing segment (slug)
            segments = [s for s in path.strip("/").split("/") if s]
            if not segments:
                return "support documentation"
            slug = segments[-1]
            # Strip numbers/IDs commonly prepended in CMS slugs (e.g., 12000006843-)
            cleaned = re.sub(r"^\d+[-_]?", "", slug)
            cleaned = re.sub(r"[-_]+", " ", cleaned)
            cleaned = re.sub(r"\.[a-zA-Z0-9]+$", "", cleaned)
            return cleaned.strip() or "support solutions"
        except Exception:
            return "support solutions"

    @staticmethod
    def validate_replacement_url(candidate_url: Optional[str], original_url: str) -> bool:
        """Safely validate that candidate replacement URL is well-formed, safe, and distinct."""
        if not candidate_url or candidate_url.strip() == original_url.strip():
            return False
        try:
            p = urlparse(candidate_url)
            if p.scheme not in ("http", "https"):
                return False
            if not p.netloc:
                return False
            return True
        except Exception:
            return False

    async def heal_url_failure(
        self,
        url: str,
        error_code: int,
        error_context: str = "",
    ) -> SelfHealingResult:
        """Analyze a 300/404 failure and discover alternative relative documents."""
        search_terms = self.extract_search_terms(url)
        logger.info(
            "AI Self-Healing triggered for HTTP %d on URL %s. Derived query: '%s'",
            error_code,
            url,
            search_terms,
        )

        relative_docs: List[Dict[str, Any]] = []
        resolved_url: Optional[str] = None

        # 1. Search RAG engine if available
        if self.rag_engine is not None:
            try:
                hits = self.rag_engine.search_knowledge(search_terms, top_k=3)
                for h in hits:
                    u = h.get("url")
                    if u and u != url:
                        relative_docs.append({
                            "url": u,
                            "title": h.get("title") or "Relative Document",
                            "score": h.get("score", 0.0),
                            "preview": (h.get("text") or "")[:150],
                        })
            except Exception as exc:
                logger.warning("RAG search failed during self-healing: %s", exc)

        # 2. Search state store article content if relative_docs is empty
        if not relative_docs and self.state_store is not None:
            try:
                articles = self.state_store.list_articles(limit=20)
                terms = [w.lower() for w in search_terms.split() if len(w) > 3]
                for art in articles:
                    art_url = art.get("url")
                    art_title = (art.get("title") or "").lower()
                    if art_url and art_url != url:
                        match_count = sum(1 for term in terms if term in art_title or term in art_url.lower())
                        if match_count > 0:
                            relative_docs.append({
                                "url": art_url,
                                "title": art.get("title") or "Related Article",
                                "score": float(match_count),
                                "preview": f"Related to {search_terms}",
                            })
            except Exception as exc:
                logger.warning("State store article scan failed during self-healing: %s", exc)

        # 3. Formulate resolution with safe URL validation
        candidate_url = relative_docs[0]["url"] if relative_docs else None
        if candidate_url and self.validate_replacement_url(candidate_url, url):
            resolved_url = candidate_url
            best_title = relative_docs[0]["title"]
            resolution_notes = (
                f"Autonomous Remediation: HTTP {error_code} handled. "
                f"Knowledge Brain self-search identified '{best_title}' ({resolved_url}) "
                f"as the primary replacement document with {len(relative_docs)} relative matches."
            )
            resolved = True
        else:
            resolved_url = None
            resolution_notes = (
                f"Self-Healing Warning: HTTP {error_code} detected on {url}. "
                f"Searched for '{search_terms}', but no closely related documents were found. "
                f"Flagged for administrator review."
            )
            resolved = False

        # 4. Record to state store audit log
        if self.state_store is not None:
            try:
                self.state_store.record_self_healing(
                    url=url,
                    error_code=error_code,
                    resolved_url=resolved_url,
                    resolution_notes=resolution_notes,
                )
            except Exception as exc:
                logger.error("Failed to save self-healing audit log: %s", exc)

        return SelfHealingResult(
            url=url,
            error_code=error_code,
            resolved=resolved,
            resolved_url=resolved_url,
            relative_docs=relative_docs,
            resolution_notes=resolution_notes,
        )
