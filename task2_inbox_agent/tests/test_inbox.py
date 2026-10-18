"""Automated Unit and Integration Tests for Task 2: Inbox Agent."""

import pytest
from fastapi.testclient import TestClient

from task2_inbox_agent.app.audit_store import InboxAuditStore
from task2_inbox_agent.app.autonomy import AutonomyEngine
from task2_inbox_agent.app.classifier import EmailClassifier
from task2_inbox_agent.app.config import settings
from task2_inbox_agent.app.drafter import ResponseDrafter
from task2_inbox_agent.app.generator import generate_synthetic_emails
from task2_inbox_agent.app.main import app
from task2_inbox_agent.app.models import (
    CompanyVoice,
    CustomerEmail,
    DraftStatus,
    EmailCategory,
    EmailPriority,
)
from task2_inbox_agent.app.similarity import calculate_draft_similarity, levenshtein_distance


@pytest.fixture
def test_client(tmp_path, monkeypatch):
    """Hermetic test client for Task 2."""
    test_db = str(tmp_path / "test_inbox_audit.db")
    isolated_store = InboxAuditStore(test_db)
    isolated_autonomy = AutonomyEngine()

    import task2_inbox_agent.app.main as m
    monkeypatch.setattr(m, "_store", isolated_store)
    monkeypatch.setattr(m, "_autonomy", isolated_autonomy)

    # Seed initial emails
    emails = generate_synthetic_emails()
    isolated_store.save_emails(emails[:10])

    with TestClient(app) as client:
        yield client


def test_synthetic_email_generation():
    """Requirement: Generate at least 50 realistic synthetic customer emails."""
    emails = generate_synthetic_emails()
    assert len(emails) >= 50

    categories = {e.category for e in emails}
    assert EmailCategory.PRODUCT_QUESTION in categories
    assert EmailCategory.PROJECT_REQUEST in categories
    assert EmailCategory.REFUND in categories
    assert EmailCategory.COMPLAINT in categories
    assert EmailCategory.QUOTE in categories
    assert EmailCategory.FOLLOW_UP in categories

    for e in emails:
        assert e.id.startswith("EML-")
        assert "@" in e.sender_email
        assert len(e.subject) > 5
        assert len(e.body) > 20


def test_email_classification():
    """Requirement: Classify requests including product questions, project requests, refunds, complaints, quotes, follow-ups."""
    classifier = EmailClassifier()

    refund_email = CustomerEmail(
        id="T-1",
        sender_name="Alice",
        sender_email="alice@test.com",
        subject="Requesting immediate refund for double charge",
        body="My card was charged twice for the monthly plan. Please refund the extra charge.",
    )
    c1 = classifier.classify(refund_email)
    assert c1.category == EmailCategory.REFUND
    assert c1.priority in (EmailPriority.HIGH, EmailPriority.URGENT)

    complaint_email = CustomerEmail(
        id="T-2",
        sender_name="Bob",
        sender_email="bob@test.com",
        subject="URGENT: Entire directory search is broken and frozen!",
        body="This is unacceptable, our users are getting 502 errors and search is completely broken.",
    )
    c2 = classifier.classify(complaint_email)
    assert c2.category == EmailCategory.COMPLAINT
    assert c2.priority == EmailPriority.URGENT

    quote_email = CustomerEmail(
        id="T-3",
        sender_name="Carol",
        sender_email="carol@test.com",
        subject="RFP: Request for price quote on 10,000 member directory",
        body="Please send pricing options and volume discount for enterprise setup.",
    )
    c3 = classifier.classify(quote_email)
    assert c3.category == EmailCategory.QUOTE


def test_transparent_similarity_and_levenshtein():
    """Requirement: Measure draft similarity and human edits using transparent rules."""
    s1 = "Hello Sarah, thank you for reaching out. We will process your request."
    s2 = "Hello Sarah, thank you for reaching out. We will process your request."
    res1 = calculate_draft_similarity(s1, s2)
    assert res1["similarity_score"] == 1.0
    assert res1["is_unchanged"] is True
    assert res1["edit_distance"] == 0

    # Minor punctuation change
    s3 = "Hello Sarah, thank you for reaching out! We will process your request."
    res2 = calculate_draft_similarity(s1, s3)
    assert res2["similarity_score"] > 0.95
    assert res2["is_unchanged"] is True

    # Substantive change by human
    s4 = "Hello Sarah, unfortunately we cannot process your request due to policy violation."
    res3 = calculate_draft_similarity(s1, s4)
    assert res3["similarity_score"] < 0.90
    assert res3["is_unchanged"] is False
    assert res3["added_words"] > 0


@pytest.mark.asyncio
async def test_response_drafter_voices():
    """Requirement: Draft customer replies in a configurable company voice."""
    drafter = ResponseDrafter()
    email = CustomerEmail(
        id="T-4",
        sender_name="David Miller",
        sender_email="david@test.com",
        subject="Double charge on my credit card",
        body="I was double billed this morning. Please help.",
        category=EmailCategory.REFUND,
    )
    classifier = EmailClassifier()
    classification = classifier.classify(email)

    # Test Empathetic Voice
    draft_emp = await drafter.draft_response(email, classification, voice=CompanyVoice.EMPATHETIC)
    assert "apologize" in draft_emp.original_draft.lower()
    assert draft_emp.voice == CompanyVoice.EMPATHETIC

    # Test Concise Voice
    draft_con = await drafter.draft_response(email, classification, voice=CompanyVoice.CONCISE)
    assert "Hi David," in draft_con.original_draft
    assert draft_con.voice == CompanyVoice.CONCISE


def test_strict_20_consecutive_approvals_rule(monkeypatch):
    """Requirement: Allow autonomous sending ONLY after 20 consecutive unchanged, human-approved drafts for the same category."""
    engine = AutonomyEngine(streak_threshold=20)
    category = EmailCategory.PRODUCT_QUESTION

    # Ensure master switch is simulated as enabled
    monkeypatch.setattr(settings, "autonomous_sending_enabled", True)

    # 1 to 19 unchanged approvals -> must NOT authorize autonomous sending
    for i in range(1, 20):
        status = engine.record_approval(category, is_unchanged=True)
        assert status.consecutive_unchanged_count == i
        can_send, msg = engine.can_send_autonomously(category)
        assert can_send is False
        assert "requires 20 consecutive" in msg

    # 20th consecutive unchanged approval -> must grant autonomous sending!
    status_20 = engine.record_approval(category, is_unchanged=True)
    assert status_20.consecutive_unchanged_count == 20
    assert status_20.autonomous_enabled is True

    can_send_20, msg_20 = engine.can_send_autonomously(category)
    assert can_send_20 is True
    assert "authorized" in msg_20.lower()


def test_automatic_revocation_on_quality_drop(monkeypatch):
    """Requirement: Automatically revoke autonomous sending permission when quality drops."""
    engine = AutonomyEngine(streak_threshold=20)
    category = EmailCategory.REFUND
    monkeypatch.setattr(settings, "autonomous_sending_enabled", True)

    # Reach 20 approvals
    for _ in range(20):
        engine.record_approval(category, is_unchanged=True)

    can_send, _ = engine.can_send_autonomously(category)
    assert can_send is True

    # 21st draft receives human edit -> immediate revocation!
    status_revoked = engine.record_approval(category, is_unchanged=False)
    assert status_revoked.autonomous_enabled is False
    assert status_revoked.consecutive_unchanged_count == 0  # Streak reset
    assert "REVOKED" in status_revoked.revocation_reason

    can_send_now, msg_now = engine.can_send_autonomously(category)
    assert can_send_now is False


def test_default_safe_human_approval_boundary(monkeypatch):
    """Requirement: Default to human approval. Never send real customer emails without explicit authorization."""
    engine = AutonomyEngine(streak_threshold=20)
    category = EmailCategory.QUOTE

    # Master switch disabled (Default setting)
    monkeypatch.setattr(settings, "autonomous_sending_enabled", False)

    for _ in range(25):
        engine.record_approval(category, is_unchanged=True)

    # Even with 25 unchanged approvals, master policy switch prevents autonomous dispatch
    can_send, msg = engine.can_send_autonomously(category)
    assert can_send is False
    assert "globally disabled" in msg.lower()


def test_inbox_api_endpoints(test_client):
    """Verify Inbox Agent REST API and review workflow."""
    # 1. Health check
    res_h = test_client.get("/health")
    assert res_h.status_code == 200
    assert res_h.json()["service"] == "task2_inbox_agent"

    # 2. List seeded emails
    res_e = test_client.get("/api/v1/emails")
    assert res_e.status_code == 200
    emails = res_e.json()
    assert len(emails) >= 1
    first_id = emails[0]["id"]

    # 3. Generate draft
    res_d = test_client.post(f"/api/v1/emails/{first_id}/draft?voice=professional")
    assert res_d.status_code == 200
    draft = res_d.json()
    assert len(draft["original_draft"]) > 20
    assert draft["email_id"] == first_id

    # 4. Review and approve unchanged
    res_r = test_client.post(
        f"/api/v1/emails/{first_id}/review",
        json={
            "email_id": first_id,
            "action": "approve",
            "edited_draft": draft["original_draft"],
            "feedback_notes": "Perfect solution draft.",
        },
    )
    assert res_r.status_code == 200
    review_res = res_r.json()
    assert review_res["status"] == "approved"
    assert review_res["is_unchanged"] is True
    assert review_res["consecutive_streak"] >= 1

    # 5. Check audit logs
    res_a = test_client.get("/api/v1/audit/logs")
    assert res_a.status_code == 200
    logs = res_a.json()
    assert len(logs) >= 1
    assert logs[0]["email_id"] == first_id

    # 6. Export reply
    res_exp = test_client.get(f"/api/v1/emails/{first_id}/export")
    assert res_exp.status_code == 200
    assert "BusinessLabs Support" in res_exp.text


def test_inbox_edited_draft_resets_streak(test_client):
    """Requirement: Approving unchanged increments category streak (+1); editing resets streak to 0."""
    res_e = test_client.get("/api/v1/emails")
    emails = res_e.json()
    assert len(emails) >= 2
    email1 = emails[0]["id"]
    email2 = emails[1]["id"]

    # Draft and approve unchanged for email 1
    d1 = test_client.post(f"/api/v1/emails/{email1}/draft?voice=professional").json()
    r1 = test_client.post(
        f"/api/v1/emails/{email1}/review",
        json={"email_id": email1, "action": "approve", "edited_draft": d1["original_draft"]},
    ).json()
    assert r1["is_unchanged"] is True
    assert r1["consecutive_streak"] == 1

    # Draft and edit substantially for email 2
    d2 = test_client.post(f"/api/v1/emails/{email2}/draft?voice=professional").json()
    substantially_edited = "Completely rewritten reply by customer support agent."
    r2 = test_client.post(
        f"/api/v1/emails/{email2}/review",
        json={"email_id": email2, "action": "approve", "edited_draft": substantially_edited},
    ).json()
    assert r2["is_unchanged"] is False
    assert r2["similarity_score"] < 0.85
    assert r2["consecutive_streak"] == 0  # Streak must be reset to 0!


def test_draft_schema_fields(test_client):
    """Requirement: DraftResponse must contain original_draft string and never be undefined or null."""
    res_e = test_client.get("/api/v1/emails")
    first_id = res_e.json()[0]["id"]
    res_d = test_client.post(f"/api/v1/emails/{first_id}/draft?voice=concise")
    assert res_d.status_code == 200
    data = res_d.json()
    assert "original_draft" in data
    assert isinstance(data["original_draft"], str)
    assert len(data["original_draft"]) > 10
    assert data["original_draft"] != "undefined"
    assert data["original_draft"] != "null"


def test_email_priority_values():
    """Requirement: Priority must conform to standard conventions (low, medium, high, urgent)."""
    valid_priorities = {"low", "medium", "high", "urgent"}
    emails = generate_synthetic_emails()
    for e in emails:
        assert e.priority.value in valid_priorities

