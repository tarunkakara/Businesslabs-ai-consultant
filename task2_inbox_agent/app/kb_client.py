"""HTTP Client for querying Task 1: Knowledge Brain REST API."""

import logging
from typing import Any, Dict, List, Optional
import httpx

from task2_inbox_agent.app.config import settings

logger = logging.getLogger("inbox_agent.kb_client")


class KnowledgeBrainClient:
    """Client for retrieving grounded facts from Task 1 Knowledge Brain REST API."""

    def __init__(self, base_url: Optional[str] = None, timeout_seconds: float = 8.0):
        self.base_url = (base_url or settings.knowledge_brain_api_url).rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def query(self, question: str, top_k: int = 3) -> Dict[str, Any]:
        """Call POST /api/v1/query on Task 1 service."""
        endpoint = f"{self.base_url}/api/v1/query"
        payload = {"question": question, "top_k": top_k}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                res = await client.post(endpoint, json=payload)
                if res.status_code == 200:
                    return res.json()
                logger.warning("Knowledge Brain API returned HTTP %d: %s", res.status_code, res.text)
        except Exception as exc:
            logger.info("Knowledge Brain HTTP call failed (%s); invoking in-process engine fallback.", exc)

        # In-process fallback if HTTP service is not actively listening on port 8000
        return await self._in_process_fallback(question, top_k)

    async def _in_process_fallback(self, question: str, top_k: int) -> Dict[str, Any]:
        """Direct in-process query to avoid hard external server dependency during isolated tests."""
        try:
            from task1_knowledge_brain.app.rag.engine import KnowledgeBrainEngine
            engine = KnowledgeBrainEngine()
            ans = await engine.answer_question(question=question, top_k=top_k)
            return ans.model_dump()
        except Exception as exc:
            logger.error("In-process fallback failed: %s", exc)
            return {
                "question": question,
                "answer": "Unable to connect to Knowledge Brain. Please refer to support documentation.",
                "is_sufficient": False,
                "sources": [],
                "confidence_score": 0.0,
            }
