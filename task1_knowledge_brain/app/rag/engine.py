"""Knowledge Brain Engine coordinating RAG search, citations, refusal, and expert feedback."""

import logging
from typing import Any, Dict, List, Optional

from task1_knowledge_brain.app.config import settings
from task1_knowledge_brain.app.providers import get_llm_provider
from task1_knowledge_brain.app.providers.base import BaseLLMProvider, GroundedAnswer
from task1_knowledge_brain.app.rag.chunker import DocumentChunk
from task1_knowledge_brain.app.rag.expert_store import ExpertFeedbackStore
from task1_knowledge_brain.app.rag.store import RAGVectorStore

logger = logging.getLogger("knowledge_brain.rag.engine")


class KnowledgeBrainEngine:
    """Core RAG engine implementing grounded generation, source citations, and human-in-the-loop QA."""

    def __init__(
        self,
        vector_store: Optional[RAGVectorStore] = None,
        expert_store: Optional[ExpertFeedbackStore] = None,
        llm_provider: Optional[BaseLLMProvider] = None,
    ):
        self.vector_store = vector_store or RAGVectorStore()
        self.expert_store = expert_store or ExpertFeedbackStore(settings.sqlite_db_path)
        self.llm_provider = llm_provider or get_llm_provider()

    async def answer_question(self, question: str, top_k: int = 5) -> GroundedAnswer:
        """Answer question with grounding, citation verification, and fallback refusal."""
        clean_q = question.strip()

        # Step 1: Check if Human Expert has already provided an approved verified answer
        expert_match = self.expert_store.find_approved_answer(clean_q)
        if expert_match:
            logger.info("Found verified expert answer for question: '%s'", clean_q)
            return GroundedAnswer(
                question=clean_q,
                answer=(
                    f"**Verified Expert Solution** (Approved by {expert_match['expert_name']}):\n\n"
                    f"{expert_match['answer']}"
                ),
                is_grounded=True,
                is_sufficient=True,
                sources=expert_match.get("sources", []),
                confidence_score=1.0,
                needs_human_review=False,
                model_name="human-expert-verified",
                is_mock=False,
            )

        # Step 2: Query vector store for relevant documentation chunks
        retrieved_contexts = self.vector_store.search(clean_q, top_k=top_k)

        # Step 3: Check grounding threshold.
        # If no contexts or if the best context is below min_grounding_similarity, refuse to invent.
        top_score = retrieved_contexts[0]["similarity_score"] if retrieved_contexts else 0.0
        if not retrieved_contexts or top_score < settings.min_grounding_similarity:
            logger.warning(
                "Insufficient evidence for '%s' (top_score: %.2f < %.2f)",
                clean_q,
                top_score,
                settings.min_grounding_similarity,
            )
            # Log question into human review queue
            self.expert_store.log_unanswered_question(clean_q)

            # Let LLM / mock generate standard truthful refusal
            return await self.llm_provider.answer_grounded(clean_q, [])

        # Step 4: Generate grounded answer with citations
        grounded_answer = await self.llm_provider.answer_grounded(clean_q, retrieved_contexts)

        # If LLM itself decided context was insufficient, log for expert review
        if not grounded_answer.is_sufficient:
            self.expert_store.log_unanswered_question(clean_q)

        return grounded_answer

    def submit_expert_approval(
        self,
        question: str,
        answer: str,
        expert_name: str = "Human Expert",
        sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Submit approved answer from human expert and index it into vector store."""
        approved = self.expert_store.approve_answer(
            question=question,
            answer=answer,
            expert_name=expert_name,
            sources=sources,
        )

        # Also index into ChromaDB so semantic search picks up this expert knowledge
        expert_url = f"https://support.brilliantdirectories.com/expert-qa/{abs(hash(question)) % 1000000}"
        chunk = DocumentChunk(
            chunk_id=f"expert_{abs(hash(question)) % 1000000}",
            text=f"Expert Q&A\nQuestion: {question}\nAnswer: {answer}",
            metadata={
                "url": expert_url,
                "title": f"Expert Answer: {question[:50]}",
                "breadcrumbs": "Community Solutions > Verified Expert Answers",
                "chunk_index": 0,
                "total_chunks": 1,
                "images_json": "[]",
            },
        )
        self.vector_store.add_article_chunks(expert_url, [chunk])

        return approved
