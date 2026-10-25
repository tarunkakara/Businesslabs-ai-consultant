"""Automated Test Suite for Task 2 Inbox Agent Synchronization & Real-Time Ingestion.

Validates:
1. Existing emails load correctly in descending chronological order (newest-first).
2. New email received after initial load appears immediately upon refresh.
3. Periodic sync detects new messages without duplicating existing records.
4. Multiple concurrent sync requests are safely locked/serialized and cannot corrupt or duplicate data.
5. Gmail API errors and token-refresh failures are handled safely with clear diagnostics.
6. Read and unread emails are filtered and displayed accurately.
7. Pagination and stale history checkpoints (HTTP 404 falling back to full sync) are handled correctly.
"""

import asyncio
from datetime import datetime, timezone
import json
import time
from typing import Any, Dict, List
import uuid

import httpx
from httpx import ASGITransport, AsyncClient
import pytest

from auth import AuthStore
from server import app
from task2_inbox_agent.app.audit_store import InboxAuditStore
from task2_inbox_agent.app.gmail_client import GmailOAuthClient
from task2_inbox_agent.app.models import (
    CustomerEmail,
    DraftStatus,
    EmailCategory,
    EmailPriority,
)


@pytest.fixture
def auth_store(tmp_path):
    db_file = str(tmp_path / "test_auth_sync.db")
    return AuthStore(db_file)


@pytest.fixture
def audit_store(tmp_path):
    db_file = str(tmp_path / "test_inbox_sync.db")
    return InboxAuditStore(db_file)


@pytest.mark.asyncio
async def test_01_existing_emails_load_newest_first(audit_store):
    """1. Existing emails load correctly with newest-first chronological sorting."""
    older_email = CustomerEmail(
        id="SIM-OLD-01",
        sender_name="Old Client",
        sender_email="old@example.com",
        subject="Old Inquiry",
        body="This was sent earlier.",
        received_at="2026-10-24T10:00:00+00:00",
        priority=EmailPriority.MEDIUM,
        status=DraftStatus.UNPROCESSED,
        is_unread=False,
        is_simulated=True,
    )
    newer_email = CustomerEmail(
        id="SIM-NEW-02",
        sender_name="New Client",
        sender_email="new@example.com",
        subject="Recent Inquiry",
        body="This arrived just now.",
        received_at="2026-10-26T12:00:00+00:00",
        priority=EmailPriority.HIGH,
        status=DraftStatus.UNPROCESSED,
        is_unread=True,
        is_simulated=True,
    )

    audit_store.save_emails([older_email, newer_email])
    loaded = audit_store.list_emails(limit=10)

    assert len(loaded) == 2
    # Newest should be index 0
    assert loaded[0].id == "SIM-NEW-02"
    assert loaded[1].id == "SIM-OLD-01"


@pytest.mark.asyncio
async def test_02_new_email_appears_after_refresh(tmp_path, monkeypatch):
    """2. A new email received after initial load appears upon refresh."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register test user
        user_email = f"user_{uuid.uuid4().hex[:6]}@example.com"
        reg_res = await client.post("/api/v1/auth/register", json={
            "email": user_email,
            "password": "Password123!",
            "full_name": "Sync Tester",
        })
        assert reg_res.status_code == 200
        user_id = reg_res.json()["user"]["id"]

        # Initial email in inbox
        from task2_inbox_agent.app.main import _auth_store, _store
        _auth_store.save_gmail_token(
            user_id=user_id,
            gmail_address="user@gmail.com",
            access_token="mock_access_token",
            refresh_token="mock_refresh_token",
            expires_in=3600,
        )

        init_id = f"GMAIL-init_{uuid.uuid4().hex[:8]}"
        initial_emails = [
            CustomerEmail(
                id=init_id,
                sender_name="Alice",
                sender_email="alice@test.com",
                subject="First Message",
                body="Initial email body",
                received_at="2026-10-25T10:00:00+00:00",
                priority=EmailPriority.MEDIUM,
                status=DraftStatus.UNPROCESSED,
                user_id=user_id,
                is_unread=False,
                is_simulated=False,
            )
        ]
        _store.save_emails(initial_emails)

        # Initial check
        res1 = await client.get("/api/v1/emails?folder=inbox&source=real")
        assert res1.status_code == 200
        data1 = res1.json()
        assert any(e["id"] == init_id for e in data1)

        # Now mock Gmail API receiving a brand new email
        new_id = f"GMAIL-new_{uuid.uuid4().hex[:8]}"
        new_incoming = [
            CustomerEmail(
                id=new_id,
                sender_name="Bob Support",
                sender_email="bob@urgent.com",
                subject="Urgent Question Arrived",
                body="Please reply immediately.",
                received_at="2026-10-26T14:30:00+00:00",
                priority=EmailPriority.HIGH,
                status=DraftStatus.UNPROCESSED,
                user_id=user_id,
                is_unread=True,
                is_simulated=False,
            )
        ]

        async def mock_sync_user_emails(*args, **kwargs):
            return {
                "status": "success",
                "emails": new_incoming,
                "count": 1,
                "new_count": 1,
                "gmail_address": "user@gmail.com",
                "history_id": "12399",
                "is_incremental": True,
                "message": "Successfully received 1 new email(s).",
            }

        monkeypatch.setattr(GmailOAuthClient, "sync_user_emails", mock_sync_user_emails)

        # Trigger refresh via /api/v1/gmail/sync
        sync_res = await client.post("/api/v1/gmail/sync", json={"limit": 20})
        assert sync_res.status_code == 200
        sync_data = sync_res.json()
        assert sync_data["status"] == "success"
        assert sync_data["new_count"] == 1

        # Query inbox again: both emails should now be present, newest first
        res2 = await client.get("/api/v1/emails?folder=inbox&source=real")
        assert res2.status_code == 200
        data2 = res2.json()
        email_ids = [e["id"] for e in data2]
        assert new_id in email_ids
        assert init_id in email_ids
        # Newest should be ordered before older
        assert email_ids.index(new_id) < email_ids.index(init_id)


@pytest.mark.asyncio
async def test_03_periodic_sync_detects_new_without_duplicates(audit_store):
    """3. Periodic sync detects new messages without creating duplicate entries."""
    email1 = CustomerEmail(
        id="GMAIL-101",
        sender_name="Charlie",
        sender_email="charlie@test.com",
        subject="Sync Check",
        body="Body 1",
        received_at="2026-10-26T08:00:00+00:00",
        priority=EmailPriority.MEDIUM,
        status=DraftStatus.UNPROCESSED,
        is_unread=True,
    )

    # First sync run: 1 email inserted
    res1 = audit_store.save_emails_with_count([email1])
    assert res1["new"] == 1
    assert res1["updated"] == 0
    assert len(audit_store.list_emails()) == 1

    # Second sync run: same email returned by Gmail plus 1 new email
    email2 = CustomerEmail(
        id="GMAIL-102",
        sender_name="Diana",
        sender_email="diana@test.com",
        subject="Newer Sync Check",
        body="Body 2",
        received_at="2026-10-26T09:00:00+00:00",
        priority=EmailPriority.MEDIUM,
        status=DraftStatus.UNPROCESSED,
        is_unread=True,
    )

    res2 = audit_store.save_emails_with_count([email1, email2])
    assert res2["new"] == 1        # Only email2 is new!
    assert res2["updated"] == 1    # email1 was recognized and merged!
    assert len(audit_store.list_emails()) == 2


@pytest.mark.asyncio
async def test_04_multiple_concurrent_syncs_locked():
    """4. Multiple sync requests cannot corrupt or duplicate data due to per-user lock."""
    user_id = f"user_{uuid.uuid4().hex[:8]}"
    lock = GmailOAuthClient._get_user_lock(user_id)
    assert not lock.locked()

    # Simulate an active sync lock
    async with lock:
        assert lock.locked()
        # When a second concurrent sync arrives, it should detect active lock
        dummy_store = AuthStore()
        res = await GmailOAuthClient.sync_user_emails(user_id=user_id, auth_store=dummy_store)
        assert res["status"] == "in_progress"
        assert "already active" in res["message"]

    # Lock is released after sync finishes
    assert not lock.locked()


@pytest.mark.asyncio
async def test_05_gmail_api_errors_and_token_refresh_handled_safely(auth_store, monkeypatch):
    """5. Gmail API errors and token-refresh failures are handled safely without crash."""
    user_id = "test_user_err"

    # Save expired token without refresh token
    auth_store.save_gmail_token(
        user_id=user_id,
        gmail_address="expired@test.com",
        access_token="expired_tok",
        refresh_token=None,
        expires_in=-100,  # Expired
    )

    token, addr, err = await GmailOAuthClient.get_valid_token(user_id, auth_store)
    assert token is None
    assert "expired" in err.lower() or "reconnect" in err.lower()

    # Sync result reflects the graceful error
    res = await GmailOAuthClient.sync_user_emails(user_id, auth_store)
    assert res["status"] == "error"
    assert "expired" in res["message"].lower() or "reconnect" in res["message"].lower()


@pytest.mark.asyncio
async def test_06_read_and_unread_filters(audit_store):
    """6. Read and unread emails are displayed accurately according to filter."""
    unread_msg = CustomerEmail(
        id="EML-UNREAD-1",
        sender_name="Unread Sender",
        sender_email="unread@test.com",
        subject="Unread Subject",
        body="Unread Body",
        received_at="2026-10-26T10:00:00+00:00",
        priority=EmailPriority.HIGH,
        status=DraftStatus.UNPROCESSED,
        is_unread=True,
    )
    read_msg = CustomerEmail(
        id="EML-READ-2",
        sender_name="Read Sender",
        sender_email="read@test.com",
        subject="Read Subject",
        body="Read Body",
        received_at="2026-10-26T09:00:00+00:00",
        priority=EmailPriority.LOW,
        status=DraftStatus.UNPROCESSED,
        is_unread=False,
    )

    audit_store.save_emails([unread_msg, read_msg])

    all_emails = audit_store.list_emails()
    assert len(all_emails) == 2

    only_unread = audit_store.list_emails(is_unread=True)
    assert len(only_unread) == 1
    assert only_unread[0].id == "EML-UNREAD-1"

    only_read = audit_store.list_emails(is_unread=False)
    assert len(only_read) == 1
    assert only_read[0].id == "EML-READ-2"


@pytest.mark.asyncio
async def test_07_stale_history_checkpoint_falls_back_to_full_sync(auth_store, monkeypatch):
    """7. Stale historyId (HTTP 404 from Gmail) safely falls back to full sync and resets checkpoint."""
    user_id = "test_user_stale"
    auth_store.save_gmail_token(
        user_id=user_id,
        gmail_address="user@gmail.com",
        access_token="valid_token",
        refresh_token="valid_refresh",
        expires_in=3600,
        history_id="stale_12345",
    )

    # Mock HTTP responses:
    # 1. /history with staleHistoryId returns 404
    # 2. /messages full sync returns 1 message
    # 3. /messages/{id} returns message detail
    # 4. /profile returns new historyId 99999
    call_log = []

    def mock_handler(request: httpx.Request):
        url = str(request.url)
        call_log.append(url)
        if "/history" in url:
            # Gmail returns HTTP 404 when historyId is too old
            return httpx.Response(404, json={"error": {"code": 404, "message": "History id is too old"}})
        elif "/profile" in url:
            return httpx.Response(200, json={"emailAddress": "user@gmail.com", "historyId": "99999"})
        elif "/messages/" in url and not url.endswith("/messages"):
            return httpx.Response(200, json={
                "id": "msg_fallback_01",
                "threadId": "th_1",
                "labelIds": ["INBOX"],
                "internalDate": "1761400000000",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Fallback Full Sync Email"},
                        {"name": "From", "value": "Support <support@google.com>"},
                        {"name": "Date", "value": "Mon, 26 Oct 2026 12:00:00 +0000"},
                    ],
                    "body": {"data": "SGVsbG8gRnVsbCBTeW5j"},
                }
            })
        elif url.endswith("/messages") or "/messages?" in url:
            return httpx.Response(200, json={
                "messages": [{"id": "msg_fallback_01", "threadId": "th_1"}],
                "resultSizeEstimate": 1,
            })
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(mock_handler)
    orig_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: orig_async_client(transport=transport)
    )

    # Perform sync
    res = await GmailOAuthClient.sync_user_emails(user_id=user_id, auth_store=auth_store)

    assert res["status"] == "success"
    assert res["is_incremental"] is False  # Fallback to full sync occurred!
    assert res["count"] == 1
    assert res["emails"][0].id == "GMAIL-msg_fallback_01"
    assert res["emails"][0].subject == "Fallback Full Sync Email"
    assert res["history_id"] == "99999"

    # Verify updated checkpoint stored in auth_store
    sync_state = auth_store.get_gmail_sync_state(user_id)
    assert sync_state["last_history_id"] == "99999"
    assert sync_state["last_sync_status"] == "success"
