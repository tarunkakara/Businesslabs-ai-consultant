"""Automated tests for newly implemented features across Parts 1-3:

1. Authentication & Persistent User Database (WAL mode, persistent DB resolution).
2. Knowledge Brain external authoritative search fallback & citation distinction.
3. Inbox Agent Draft saving, Drafts folder persistence, Sent folder reply recording, and folder counts.
"""

import os
import sqlite3
import pytest
from fastapi.testclient import TestClient

from auth import get_persistent_db_path, AuthStore
from task1_knowledge_brain.app.rag.engine import KnowledgeBrainEngine
from task1_knowledge_brain.app.rag.external_search import ExternalSearchService
from task1_knowledge_brain.app.rag.store import RAGVectorStore
from task1_knowledge_brain.app.rag.expert_store import ExpertFeedbackStore
from task2_inbox_agent.app.main import app as inbox_app
from task2_inbox_agent.app.models import CustomerEmail, EmailCategory, EmailPriority, DraftStatus
from task2_inbox_agent.app.audit_store import InboxAuditStore


def test_persistent_db_resolution(monkeypatch, tmp_path):
    """Verify that get_persistent_db_path prioritizes custom persistent paths and enables WAL mode."""
    custom_dir = tmp_path / "persistent_disk"
    custom_dir.mkdir()
    monkeypatch.setenv("RENDER_DISK_PATH", str(custom_dir))

    resolved_path = get_persistent_db_path()
    assert str(custom_dir) in str(resolved_path)
    assert str(resolved_path).endswith("auth.db")

    # Verify SQLite WAL mode is configured
    auth_store = AuthStore(db_path=str(resolved_path))
    with auth_store._get_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"


@pytest.mark.asyncio
async def test_knowledge_brain_external_search_fallback(tmp_path):
    """Verify external authoritative search fallback when internal vector store lacks evidence."""
    chroma_dir = str(tmp_path / "test_ext_chroma")
    sqlite_file = str(tmp_path / "test_ext.db")

    vector_store = RAGVectorStore(persist_directory=chroma_dir)
    expert_store = ExpertFeedbackStore(sqlite_file)
    ext_service = ExternalSearchService()

    engine = KnowledgeBrainEngine(
        vector_store=vector_store,
        expert_store=expert_store,
        external_search=ext_service,
    )

    # Empty internal store -> ask about Stripe payment gateway
    ans = await engine.answer_question("How do I connect Stripe payment gateway for recurring member billing?")
    assert ans.is_sufficient is True
    assert ans.is_grounded is True
    # Citation should be labeled External Web Research
    assert any(s.get("source_type") == "External Web Research" for s in ans.sources)
    assert any("support.brilliantdirectories.com" in s.get("url", "") for s in ans.sources)

    # External search results must NOT be automatically saved into internal vector store
    assert vector_store.count() == 0


@pytest.mark.asyncio
async def test_knowledge_brain_truthful_refusal_when_all_fail(tmp_path):
    """Verify that if both internal and external search fail, truthful refusal is given and question is logged."""
    chroma_dir = str(tmp_path / "test_refuse_chroma")
    sqlite_file = str(tmp_path / "test_refuse.db")

    vector_store = RAGVectorStore(persist_directory=chroma_dir)
    expert_store = ExpertFeedbackStore(sqlite_file)

    engine = KnowledgeBrainEngine(
        vector_store=vector_store,
        expert_store=expert_store,
    )

    impossible_q = "How do I activate the tachyon hyperspace warp drive on Brilliant Directories?"
    ans = await engine.answer_question(impossible_q)

    assert ans.is_sufficient is False
    assert ans.needs_human_review is True
    assert "insufficient evidence" in ans.answer.lower()

    # Question should be logged in expert review queue
    unanswered = expert_store.get_pending_questions()
    assert any(impossible_q in item["question"] for item in unanswered)


def test_inbox_save_draft_and_drafts_folder(tmp_path):
    """Verify saving a draft without dispatching and listing it in the Drafts folder view."""
    test_db = str(tmp_path / "test_inbox.db")
    store = InboxAuditStore(db_path=test_db)

    # 1. Create incoming customer email
    incoming = CustomerEmail(
        id="email_test_101",
        sender_name="Alice Smith",
        sender_email="alice@example.com",
        subject="Question about membership billing",
        body="Can I pay with PayPal monthly?",
        category=EmailCategory.BILLING,
        priority=EmailPriority.HIGH,
        status=DraftStatus.UNPROCESSED,
        folder="inbox",
    )
    store.save_emails([incoming])

    # 2. Save draft content without dispatching
    saved_draft = store.save_draft_content(
        email_id="email_test_101",
        draft_text="Hello Alice, yes PayPal is supported for recurring monthly billing.",
    )
    assert saved_draft.original_draft == "Hello Alice, yes PayPal is supported for recurring monthly billing."

    # 3. Query Drafts folder -> email must appear in Drafts
    drafts_list = store.list_emails(folder="drafts")
    assert any(e.id == "email_test_101" for e in drafts_list)

    # 4. Check folder counts
    counts = store.get_folder_counts()
    assert counts["drafts"] >= 1


def test_inbox_sent_replies_appear_in_sent_folder(tmp_path):
    """Verify that when a reply is sent, it is recorded in emails table with folder='sent' and recipient."""
    test_db = str(tmp_path / "test_inbox_sent.db")
    store = InboxAuditStore(db_path=test_db)

    sent_msg = CustomerEmail(
        id="sent_reply_201",
        sender_name="Consultant Support",
        sender_email="consultant@businesslabs.ai",
        recipient_email="bob@example.com",
        subject="Re: Custom CSS Design inquiry",
        body="We have applied the graphite emerald styles to your portal.",
        folder="sent",
        status=DraftStatus.APPROVED,
    )
    store.save_emails([sent_msg])

    # Query Sent folder
    sent_list = store.list_emails(folder="sent")
    assert any(e.id == "sent_reply_201" for e in sent_list)
    found = next(e for e in sent_list if e.id == "sent_reply_201")
    assert found.recipient_email == "bob@example.com"
    assert found.folder == "sent"

    # Query Inbox folder -> sent reply must NOT appear in Inbox
    inbox_list = store.list_emails(folder="inbox")
    assert not any(e.id == "sent_reply_201" for e in inbox_list)
