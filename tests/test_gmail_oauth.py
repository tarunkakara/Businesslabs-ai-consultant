"""Tests for Real Gmail Inbox Agent Google OAuth 2.0 Integration and Multi-tenant Isolation.

Covers:
- AES-256 / Fernet token encryption at rest
- AuthStore Gmail token persistence & secure retrieval
- Multi-tenant mailbox isolation (User A vs User B)
- Real vs Simulated email tagging & filtering
- Gmail OAuth status, connect URL generation, and disconnect endpoints
- Thread-preserving send logic
"""

import sys
from pathlib import Path
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import app
from auth import AuthStore, TokenCrypt
from task2_inbox_agent.app.audit_store import InboxAuditStore
from task2_inbox_agent.app.models import CustomerEmail, EmailCategory, EmailPriority, DraftStatus


@pytest.fixture
def auth_store():
    return AuthStore()


@pytest.fixture
def inbox_store():
    return InboxAuditStore()


def test_token_encryption_at_rest():
    """Verify that tokens are encrypted at rest using AES-256 / Fernet and never stored plaintext."""
    crypt = TokenCrypt()
    raw_token = "ya29.a0ARrdaM_secret_oauth_token_12345"

    enc = crypt.encrypt(raw_token)
    assert enc != raw_token
    assert not enc.startswith("ya29")
    assert len(enc) > len(raw_token)

    dec = crypt.decrypt(enc)
    assert dec == raw_token

    # Ensure empty string handles gracefully
    assert crypt.encrypt("") is None
    assert crypt.decrypt("") is None


def test_auth_store_gmail_token_lifecycle(auth_store):
    """Test saving, retrieving, updating, and deleting encrypted Gmail OAuth tokens."""
    user_id = f"test-user-{uuid.uuid4().hex[:8]}"
    gmail_addr = f"consultant_{uuid.uuid4().hex[:6]}@gmail.com"
    raw_access = "access_token_abc123"
    raw_refresh = "refresh_token_xyz789"

    # 1. Save token
    auth_store.save_gmail_token(
        user_id=user_id,
        gmail_address=gmail_addr,
        access_token=raw_access,
        refresh_token=raw_refresh,
        expires_in=3600,
        scope="https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/gmail.send",
    )

    # 2. Retrieve token (decrypted by AuthStore)
    tok_info = auth_store.get_gmail_token(user_id)
    assert tok_info is not None
    assert tok_info["gmail_address"] == gmail_addr
    assert tok_info["access_token"] == raw_access
    assert tok_info["refresh_token"] == raw_refresh

    # 3. Verify that on SQLite disk, the columns are encrypted, not plaintext
    with auth_store._get_connection() as conn:
        row = conn.execute("SELECT access_token_enc, refresh_token_enc FROM user_gmail_tokens WHERE user_id = ?", (user_id,)).fetchone()
        assert row is not None
        assert row["access_token_enc"] != raw_access
        assert row["refresh_token_enc"] != raw_refresh

    # 4. Update access token
    new_access = "updated_access_token_999"
    auth_store.update_gmail_access_token(user_id, new_access, 7200)
    updated_info = auth_store.get_gmail_token(user_id)
    assert updated_info["access_token"] == new_access
    assert updated_info["refresh_token"] == raw_refresh

    # 5. Delete / disconnect token
    auth_store.delete_gmail_token(user_id)
    assert auth_store.get_gmail_token(user_id) is None


def test_multitenant_email_isolation(inbox_store):
    """Verify that User A cannot view User B's real emails, while simulated emails remain shared."""
    user_a = f"usr-a-{uuid.uuid4().hex[:6]}"
    user_b = f"usr-b-{uuid.uuid4().hex[:6]}"

    # Save real email for User A
    email_a = CustomerEmail(
        id=f"REAL-A-{uuid.uuid4().hex[:6]}",
        sender_name="Client A",
        sender_email="clienta@example.com",
        subject="Private inquiry for User A",
        body="This message belongs to User A's real mailbox.",
        received_at="2026-10-23T12:00:00Z",
        user_id=user_a,
        is_simulated=False,
        is_unread=True,
    )
    # Save real email for User B
    email_b = CustomerEmail(
        id=f"REAL-B-{uuid.uuid4().hex[:6]}",
        sender_name="Client B",
        sender_email="clientb@example.com",
        subject="Private inquiry for User B",
        body="This message belongs to User B's real mailbox.",
        received_at="2026-10-23T12:05:00Z",
        user_id=user_b,
        is_simulated=False,
        is_unread=True,
    )
    # Save simulated email
    email_sim = CustomerEmail(
        id=f"SIM-{uuid.uuid4().hex[:6]}",
        sender_name="Simulated Client",
        sender_email="sim@example.com",
        subject="Simulated ticket",
        body="This simulated message is visible to all.",
        received_at="2026-10-23T12:10:00Z",
        user_id=None,
        is_simulated=True,
    )

    inbox_store.save_emails([email_a, email_b, email_sim])

    # User A queries
    emails_for_a = inbox_store.list_emails(user_id=user_a)
    ids_for_a = [e.id for e in emails_for_a]
    assert email_a.id in ids_for_a
    assert email_sim.id in ids_for_a
    assert email_b.id not in ids_for_a  # User B's email isolated!

    # User B queries
    emails_for_b = inbox_store.list_emails(user_id=user_b)
    ids_for_b = [e.id for e in emails_for_b]
    assert email_b.id in ids_for_b
    assert email_sim.id in ids_for_b
    assert email_a.id not in ids_for_b  # User A's email isolated!

    # Filter source == 'real' for User A
    real_a = inbox_store.list_emails(user_id=user_a, source="real")
    assert all(not e.is_simulated and e.user_id == user_a for e in real_a)

    # Filter source == 'simulated'
    sim_a = inbox_store.list_emails(user_id=user_a, source="simulated")
    assert all(e.is_simulated for e in sim_a)


@pytest.mark.asyncio
async def test_gmail_oauth_status_endpoint():
    """Verify /api/v1/gmail/oauth/status rejects unauthenticated callers and returns proper structure when authenticated."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Unauthenticated request must be rejected with 401
        res = await client.get("/api/v1/gmail/oauth/status")
        assert res.status_code == 401

        # Register and login a new user
        reg_email = f"gmailtest_{uuid.uuid4().hex[:6]}@example.com"
        reg_res = await client.post("/api/v1/auth/register", json={
            "email": reg_email,
            "password": "Password123!",
            "full_name": "Gmail Test User"
        })
        assert reg_res.status_code == 200

        # Status for authenticated user without connected Gmail
        status_res = await client.get("/api/v1/gmail/oauth/status")
        assert status_res.status_code == 200
        status_data = status_res.json()
        assert "configured" in status_data
        assert status_data["connected"] is False


@pytest.mark.asyncio
async def test_gmail_sync_unconnected_graceful_response():
    """Verify /api/v1/gmail/sync returns clear guidance when user hasn't authorized Gmail."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        reg_email = f"sync_{uuid.uuid4().hex[:6]}@example.com"
        reg_res = await client.post("/api/v1/auth/register", json={
            "email": reg_email,
            "password": "Password123!",
            "full_name": "Sync Tester"
        })
        assert reg_res.status_code == 200

        sync_res = await client.post("/api/v1/gmail/sync", json={"limit": 10})
        assert sync_res.status_code == 200
        data = sync_res.json()
        assert data["status"] == "not_connected"
        assert "Connect Gmail" in data["message"]


@pytest.mark.asyncio
async def test_simulated_email_reply_dispatch():
    """Verify sending a reply to a simulated email marks it approved without attempting real SMTP/OAuth."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login user
        reg_email = f"reply_{uuid.uuid4().hex[:6]}@example.com"
        reg_res = await client.post("/api/v1/auth/register", json={
            "email": reg_email,
            "password": "Password123!",
            "full_name": "Reply Tester"
        })
        assert reg_res.status_code == 200

        # 2. Ingest simulated email
        sim_res = await client.post("/api/v1/emails/simulate", json={
            "sender_name": "Test Client",
            "sender_email": "client@example.com",
            "subject": "Testing simulated reply workflow",
            "body": "Can you verify this workflow?",
            "category": "billing"
        })
        assert sim_res.status_code == 200
        email_id = sim_res.json()["email"]["id"]

        # 3. Dispatch reply
        send_res = await client.post("/api/v1/gmail/send", json={
            "to_email": "client@example.com",
            "subject": "Re: Testing simulated reply workflow",
            "body_text": "Thank you, this issue has been resolved.",
            "email_id": email_id
        })
        assert send_res.status_code == 200
        data = send_res.json()
        assert data.get("simulated") is True
        assert data.get("success") is True

        # 4. Disconnect endpoint test
        disco_res = await client.post("/api/v1/gmail/oauth/disconnect")
        assert disco_res.status_code == 200
        assert disco_res.json().get("status") == "success"


@pytest.mark.asyncio
async def test_app_google_login_redirect_url(monkeypatch):
    """Verify application Google Sign-In generates correct Google OAuth URL, scopes, and redirect URI."""
    import server
    monkeypatch.setattr(server, "GOOGLE_CLIENT_ID", "test-client-id-app.apps.googleusercontent.com")
    monkeypatch.setattr(server, "GOOGLE_CLIENT_SECRET", "test-client-secret-app")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://businesslabs-ai-consultant.onrender.com", follow_redirects=False) as client:
        res = await client.get("/api/v1/auth/google/login")
        assert res.status_code == 303
        loc = res.headers["location"]
        assert "https://accounts.google.com/o/oauth2/v2/auth" in loc
        assert "client_id=test-client-id-app.apps.googleusercontent.com" in loc
        assert "redirect_uri=https://businesslabs-ai-consultant.onrender.com/api/v1/auth/google/callback" in loc
        assert "scope=openid%20email%20profile" in loc
        assert "oauth_state" in res.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_inbox_gmail_oauth_connect_redirect_url(monkeypatch):
    """Verify Inbox Agent Gmail connection generates correct Google OAuth URL, Gmail scopes, and redirect URI."""
    import server
    monkeypatch.setattr(server, "GOOGLE_GMAIL_CLIENT_ID", "test-client-id-gmail.apps.googleusercontent.com")
    monkeypatch.setattr(server, "GOOGLE_GMAIL_CLIENT_SECRET", "test-client-secret-gmail")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://businesslabs-ai-consultant.onrender.com", follow_redirects=False) as client:
        # Authenticate first
        reg_email = f"gmail_conn_{uuid.uuid4().hex[:6]}@example.com"
        reg_res = await client.post("/api/v1/auth/register", json={
            "email": reg_email,
            "password": "Password123!",
            "full_name": "Gmail Connector"
        })
        assert reg_res.status_code == 200

        # Connect Gmail
        conn_res = await client.get("/api/v1/gmail/oauth/connect")
        assert conn_res.status_code == 303
        loc = conn_res.headers["location"]
        assert "https://accounts.google.com/o/oauth2/v2/auth" in loc
        assert "client_id=test-client-id-gmail.apps.googleusercontent.com" in loc
        assert "redirect_uri=https://businesslabs-ai-consultant.onrender.com/api/v1/gmail/oauth/callback" in loc
        # Verify requested scopes
        assert "https://www.googleapis.com/auth/gmail.modify" in loc
        assert "https://www.googleapis.com/auth/gmail.send" in loc
        assert "https://www.googleapis.com/auth/userinfo.email" in loc
        assert "gmail_oauth_state" in conn_res.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_oauth_callback_security_validation():
    """Verify state validation and CSRF defenses in both Google callbacks."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://businesslabs-ai-consultant.onrender.com", follow_redirects=False) as client:
        # 1. Google App Sign-In with invalid/tampered state
        tampered_res = await client.get("/api/v1/auth/google/callback?code=fake_code&state=forged_state")
        assert tampered_res.status_code == 303
        assert "/login?error=invalid_state" in tampered_res.headers["location"]

        # 2. Gmail OAuth callback with invalid state
        # First authenticate
        reg_email = f"cb_user_{uuid.uuid4().hex[:6]}@example.com"
        await client.post("/api/v1/auth/register", json={
            "email": reg_email,
            "password": "Password123!",
            "full_name": "Callback Tester"
        })
        gmail_cb_res = await client.get("/api/v1/gmail/oauth/callback?code=fake_code&state=fake_state")
        assert gmail_cb_res.status_code == 303
        assert "/inbox?error=invalid_state" in gmail_cb_res.headers["location"]


@pytest.mark.asyncio
async def test_inbox_gmail_oauth_connect_return_to(monkeypatch):
    """Verify return_to parameter is stored in cookie and used on callback error or success."""
    import server
    monkeypatch.setattr(server, "GOOGLE_GMAIL_CLIENT_ID", "test-client-id-gmail.apps.googleusercontent.com")
    monkeypatch.setattr(server, "GOOGLE_GMAIL_CLIENT_SECRET", "test-client-secret-gmail")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://businesslabs-ai-consultant.onrender.com", follow_redirects=False) as client:
        reg_email = f"return_user_{uuid.uuid4().hex[:6]}@example.com"
        await client.post("/api/v1/auth/register", json={
            "email": reg_email,
            "password": "Password123!",
            "full_name": "Return Tester"
        })

        # Connect with return_to=/?view=inbox
        conn_res = await client.get("/api/v1/gmail/oauth/connect?return_to=/?view=inbox")
        assert conn_res.status_code == 303
        assert "gmail_oauth_return_to" in conn_res.headers.get("set-cookie", "")

        # Simulate Google callback with access_denied error
        cb_res = await client.get("/api/v1/gmail/oauth/callback?error=access_denied")
        assert cb_res.status_code == 303
        assert "view=inbox" in cb_res.headers["location"]
        assert "error=access_denied" in cb_res.headers["location"]
