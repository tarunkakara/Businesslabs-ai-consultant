"""Unit and integration tests for RAG Chunker, Vector Store, Purging, and Expert Feedback."""

import pytest
from task1_knowledge_brain.app.rag.chunker import DocumentChunker
from task1_knowledge_brain.app.rag.engine import KnowledgeBrainEngine
from task1_knowledge_brain.app.rag.expert_store import ExpertFeedbackStore
from task1_knowledge_brain.app.rag.store import RAGVectorStore


def test_document_chunker():
    """Verify document chunker splits paragraphs and preserves images."""
    chunker = DocumentChunker(target_chunk_size=300, chunk_overlap=50)
    body = (
        "Brilliant Directories allows directory owners to create specialized member listings.\n\n"
        "Members can select membership plans, upload logos, and define service areas.\n\n"
        "To accept member subscription payments, directory owners can connect Stripe or PayPal gateways in the Finance section."
    )
    images = [
        {"url": "https://support.bd.com/img1.png", "alt": "Membership plan settings"}
    ]

    chunks = chunker.chunk_article(
        url="https://support.bd.com/memberships",
        title="Membership Plans Overview",
        breadcrumbs="Finance > Memberships",
        body_text=body,
        images=images,
    )

    assert len(chunks) >= 1
    first_chunk = chunks[0]
    assert first_chunk.metadata["url"] == "https://support.bd.com/memberships"
    assert "Membership Plans Overview" in first_chunk.text
    assert "Membership plan settings" in first_chunk.metadata["images_json"]


def test_expert_feedback_store(tmp_path):
    """Verify logging unanswered questions and approving human expert answers."""
    db_file = str(tmp_path / "test_expert.db")
    store = ExpertFeedbackStore(db_file)

    # 1. Log unanswered question
    store.log_unanswered_question("How do I migrate members from WordPress to BD?")
    pending = store.get_pending_questions()
    assert len(pending) == 1
    assert pending[0]["question"] == "How do I migrate members from WordPress to BD?"
    assert pending[0]["status"] == "pending"

    # 2. Approve human answer
    res = store.approve_answer(
        question="How do I migrate members from WordPress to BD?",
        answer="Export WordPress users as CSV, map column headers in BD Import Tool, and click Upload.",
        expert_name="Senior BD Architect",
        sources=[{"title": "CSV Import Tool Guide", "url": "https://support.bd.com/import-guide"}],
    )
    assert res["expert_name"] == "Senior BD Architect"

    # Verify marked resolved
    pending_after = store.get_pending_questions()
    assert len(pending_after) == 0

    # Verify retrieval
    found = store.find_approved_answer("How do I migrate members from WordPress to BD?")
    assert found is not None
    assert "Export WordPress users" in found["answer"]
    assert len(found["sources"]) == 1


def test_vector_store_indexing_and_purging(tmp_path):
    """Verify indexing article chunks and purging removed articles from answers."""
    chroma_dir = str(tmp_path / "test_chroma")
    vector_store = RAGVectorStore(persist_directory=chroma_dir)
    chunker = DocumentChunker()

    url = "https://support.bd.com/article-to-remove"
    chunks = chunker.chunk_article(
        url=url,
        title="Deprecated Legacy Add-on",
        breadcrumbs="Add-ons > Deprecated",
        body_text="This add-on has been replaced by the modern SEO booster module.",
        images=[],
    )

    vector_store.add_article_chunks(url, chunks)
    assert vector_store.count() >= 1

    # Search finds it
    results = vector_store.search("modern SEO booster", top_k=2)
    assert len(results) > 0
    assert results[0]["url"] == url

    # Purge article (simulating removed/outdated page)
    purged_count = vector_store.purge_article(url)
    assert purged_count > 0
    assert vector_store.count() == 0

    # Search should no longer return removed article
    results_after = vector_store.search("modern SEO booster", top_k=2)
    assert len(results_after) == 0


@pytest.mark.asyncio
async def test_knowledge_brain_engine_end_to_end(tmp_path):
    """Verify grounded generation, citations, refusal on unknown, and expert retrieval."""
    chroma_dir = str(tmp_path / "test_engine_chroma")
    sqlite_file = str(tmp_path / "test_engine.db")

    vector_store = RAGVectorStore(persist_directory=chroma_dir)
    expert_store = ExpertFeedbackStore(sqlite_file)
    chunker = DocumentChunker()

    engine = KnowledgeBrainEngine(
        vector_store=vector_store,
        expert_store=expert_store,
    )

    # 1. Index a known article
    known_url = "https://support.bd.com/lead-system"
    chunks = chunker.chunk_article(
        url=known_url,
        title="Lead Distribution System",
        breadcrumbs="Features > Leads",
        body_text="The Lead Distribution System allows automatic routing of incoming consumer inquiries to matching verified directory members.",
        images=[{"url": "https://support.bd.com/leads.png", "alt": "Lead routing diagram"}],
    )
    vector_store.add_article_chunks(known_url, chunks)

    # 2. Test grounded query with citation
    known_ans = await engine.answer_question("How does the Lead Distribution System work?")
    assert known_ans.is_sufficient is True
    assert known_ans.is_grounded is True
    assert "[Lead Distribution System]" in known_ans.answer
    assert known_url in known_ans.answer

    # 3. Test refusal on unknown / out-of-domain question
    unknown_ans = await engine.answer_question("Can I run quantum teleportation on Brilliant Directories?")
    assert unknown_ans.is_sufficient is False
    assert unknown_ans.needs_human_review is True
    assert "insufficient evidence" in unknown_ans.answer.lower()

    # Verify unknown query was logged in expert queue
    pending = expert_store.get_pending_questions()
    assert any("quantum teleportation" in q["question"] for q in pending)

    # 4. Test Human Expert resolution flow
    engine.submit_expert_approval(
        question="Can I run quantum teleportation on Brilliant Directories?",
        answer="No, Brilliant Directories is a web directory platform and does not support quantum physics operations.",
        expert_name="Lead Consultant",
    )

    # Ask the question again -> should immediately return human-approved answer
    resolved_ans = await engine.answer_question("Can I run quantum teleportation on Brilliant Directories?")
    assert resolved_ans.is_sufficient is True
    assert "Verified Expert Solution" in resolved_ans.answer
    assert "Lead Consultant" in resolved_ans.answer
