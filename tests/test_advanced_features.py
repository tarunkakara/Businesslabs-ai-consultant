"""Tests for Task 1 (Knowledge Brain) & Task 2 (Gmail Inbox Agent) Upgrades.

Covers:
- Nested sitemaps traversal & completion time estimates
- 3-way change lifecycle & KB2 removed archive retention
- AI self-healing on HTTP 300/404 crashes
- User authentication and profile sessions
- Real Gmail integration client & simulation
- 5-minute continuous learning loop from reviewer edits
- Category triage (Billing, Filters, Design Settings)
- Configurable autonomous sending streak threshold
"""

import sys
from pathlib import Path
import secrets
import uuid

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from httpx import ASGITransport, AsyncClient

from server import app
from task1_knowledge_brain.app.crawler.self_healing import SelfHealingAgent
from task1_knowledge_brain.app.crawler.sitemap import SitemapParser
from task1_knowledge_brain.app.crawler.state import CrawlStateStore
from task2_inbox_agent.app.autonomy import AutonomyEngine
from task2_inbox_agent.app.classifier import EmailClassifier
from task2_inbox_agent.app.gmail_client import GmailClient
from task2_inbox_agent.app.learning import ContinuousLearningEngine
from task2_inbox_agent.app.models import CustomerEmail, EmailCategory


# ==============================================================================
# Task 1: Knowledge Brain Tests
# ==============================================================================

def test_sitemap_time_estimation():
    """Verify time calculation: pages per 5 min -> hours/days ± 1 day."""
    # 50 URLs at 3 per 5 min = ~85 mins
    est1 = SitemapParser.estimate_creation_time(50, pages_per_5_min=3)
    assert est1["pages_per_5_min"] == 3
    assert est1["total_minutes"] == 85
    assert est1["total_hours"] == 1.4

    # 0 URLs
    est0 = SitemapParser.estimate_creation_time(0)
    assert est0["total_minutes"] == 0

    # 2000 URLs at 3 per 5 min = ~55.5 hours = ~2.3 days (± 1 day)
    est_large = SitemapParser.estimate_creation_time(2000, pages_per_5_min=3)
    assert est_large["total_days"] > 2.0
    assert "± 1 day" in est_large["estimate_range"]


def test_nested_sitemap_xml_parsing():
    """Verify discovering child sitemaps in sitemapindex XML and direct URLs in urlset XML."""
    index_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <sitemap><loc>https://example.com/sitemap-articles.xml</loc></sitemap>
        <sitemap><loc>https://example.com/sitemap-solutions.xml</loc></sitemap>
    </sitemapindex>
    """
    parser = SitemapParser()
    child_sitemaps, direct_urls = parser.parse_xml_both(index_xml)
    assert len(child_sitemaps) == 2
    assert "https://example.com/sitemap-articles.xml" in child_sitemaps
    assert len(direct_urls) == 0

    urlset_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://example.com/page-1</loc><lastmod>2026-10-01</lastmod></url>
        <url><loc>https://example.com/page-2</loc><lastmod>2026-10-02</lastmod></url>
    </urlset>
    """
    child_sitemaps2, direct_urls2 = parser.parse_xml_both(urlset_xml)
    assert len(child_sitemaps2) == 0
    assert len(direct_urls2) == 2


def test_kb2_removed_archive_retention(tmp_path):
    """Verify parallel rechecks policy: only archive to KB2 after exhausting threshold."""
    db_file = str(tmp_path / "test_kb2_lifecycle.db")
    store = CrawlStateStore(db_file)

    # 1. Initial sync with 2 URLs
    urls = [
        {"url": "https://example.com/doc-1", "lastmod": "2026-10-01"},
        {"url": "https://example.com/doc-2", "lastmod": "2026-10-01"},
    ]
    store.sync_sitemap_urls(urls, missing_threshold=2)
    # Mark doc-2 as crawled with content
    store.mark_crawled(
        url="https://example.com/doc-2",
        title="Payment Gateways",
        breadcrumbs=["Home", "Solutions"],
        body_text="Payment gateways documentation body.",
        images=[],
        content_hash="hash123",
    )

    # 2. Sync where doc-2 is missing (Check 1 of 2 -> Grace period, preserved in KB1)
    urls_missing = [{"url": "https://example.com/doc-1", "lastmod": "2026-10-01"}]
    counts1 = store.sync_sitemap_urls(urls_missing, missing_threshold=2)
    assert counts1["missing_pending"] == 1
    assert counts1["removed"] == 0
    # KB2 should still be empty
    assert len(store.get_kb2_archive()) == 0

    # 3. Second sync missing doc-2 (Check 2 of 2 -> Threshold reached, archived to KB2)
    counts2 = store.sync_sitemap_urls(urls_missing, missing_threshold=2)
    assert counts2["removed"] == 1
    archived = store.get_kb2_archive()
    assert len(archived) == 1
    assert archived[0]["url"] == "https://example.com/doc-2"
    assert archived[0]["title"] == "Payment Gateways"
    assert archived[0]["missing_checks_count"] == 2


@pytest.mark.asyncio
async def test_self_healing_agent(tmp_path):
    """Verify AI agent autonomous remediation on 300 / 404 crashes."""
    db_file = str(tmp_path / "test_self_healing.db")
    store = CrawlStateStore(db_file)

    # Pre-populate state store with relative article
    store.sync_sitemap_urls([{"url": "https://example.com/articles/123-supported-payment-gateways", "lastmod": "2026-10-01"}], missing_threshold=1)
    store.mark_crawled(
        url="https://example.com/articles/123-supported-payment-gateways",
        title="Supported Payment Gateways Setup",
        breadcrumbs=["Solutions"],
        body_text="Complete guide to setting up Stripe and PayPal payment gateways.",
        images=[],
        content_hash="h1",
    )

    agent = SelfHealingAgent(state_store=store)
    # Simulate a 404 crash on a broken payment gateways URL
    broken_url = "https://example.com/articles/999-broken-payment-gateways"
    result = await agent.heal_url_failure(url=broken_url, error_code=404)

    assert result.error_code == 404
    assert result.resolved is True
    assert result.resolved_url == "https://example.com/articles/123-supported-payment-gateways"
    assert "Autonomous Remediation" in result.resolution_notes

    # Verify audit log saved in DB
    logs = store.get_self_healing_logs()
    assert len(logs) == 1
    assert logs[0]["url"] == broken_url
    assert logs[0]["error_code"] == 404


@pytest.mark.asyncio
async def test_full_authentication_flow_12_cases():
    """Verify all 12 authentication lifecycle requirements explicitly."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Case 1: Register a new account with valid credentials & name
        user_email = f"consultant_{secrets.token_hex(4)}@businesslabs.ai"
        user_pwd = "StrongSecurePassword@2026"
        user_name = "Alex Mercer"

        reg_res = await client.post("/api/v1/auth/register", json={
            "full_name": user_name,
            "email": user_email.upper(),  # Test email case normalization to lowercase
            "password": user_pwd,
        })
        assert reg_res.status_code == 200
        reg_data = reg_res.json()
        assert reg_data["status"] == "success"
        assert reg_data["user"]["email"] == user_email.lower()
        assert reg_data["user"]["name"] == user_name

        # Case 2: Register with an invalid email format
        inv_email_res = await client.post("/api/v1/auth/register", json={
            "full_name": "Test User",
            "email": "not-an-email",
            "password": user_pwd,
        })
        assert inv_email_res.status_code == 400
        assert "invalid email format" in inv_email_res.json()["detail"].lower()

        # Case 3: Register with a weak password (missing special char and uppercase)
        weak_pwd_res = await client.post("/api/v1/auth/register", json={
            "full_name": "Test User",
            "email": f"weak_{secrets.token_hex(4)}@test.com",
            "password": "lowercaseandnumbers123",
        })
        assert weak_pwd_res.status_code == 400
        assert "uppercase" in weak_pwd_res.json()["detail"].lower() or "special character" in weak_pwd_res.json()["detail"].lower()

        # Case 4: Try registering the same email twice
        dup_res = await client.post("/api/v1/auth/register", json={
            "full_name": "Duplicate User",
            "email": user_email,
            "password": user_pwd,
        })
        assert dup_res.status_code == 400
        assert "already exists" in dup_res.json()["detail"].lower()

        # Case 5: Sign in with the correct credentials
        # Clear cookies first to simulate clean login
        client.cookies.clear()
        login_res = await client.post("/api/v1/auth/login", json={
            "email": user_email,
            "password": user_pwd,
        })
        assert login_res.status_code == 200
        login_data = login_res.json()
        assert login_data["status"] == "success"
        assert login_data["user"]["email"] == user_email
        assert "session_token" in client.cookies

        # Case 6: Sign in with an incorrect password and confirm the exact secure error message
        bad_login = await client.post("/api/v1/auth/login", json={
            "email": user_email,
            "password": "WrongPassword@999",
        })
        assert bad_login.status_code == 401
        assert bad_login.json()["detail"] == "Incorrect email or password. Please try again."

        # Case 7: Refresh the page / re-request and confirm the session behaves correctly
        me_res = await client.get("/api/v1/auth/me")
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data["status"] == "authenticated"
        assert me_data["user"]["email"] == user_email
        assert me_data["user"]["name"] == user_name

        # Case 8: Sign out and confirm the session is invalidated
        logout_res = await client.post("/api/v1/auth/logout")
        assert logout_res.status_code == 200
        assert logout_res.json()["status"] == "logged_out"

        # Case 9: Try opening a protected URL after logout
        post_logout_me = await client.get("/api/v1/auth/me")
        assert post_logout_me.status_code == 401

        # Case 10: Sign in using a different email account
        user2_email = f"partner_{secrets.token_hex(4)}@businesslabs.ai"
        user2_name = "Dana Scully"
        await client.post("/api/v1/auth/register", json={
            "full_name": user2_name,
            "email": user2_email,
            "password": "PartnerPassword@2026",
        })
        login2_res = await client.post("/api/v1/auth/login", json={
            "email": user2_email,
            "password": "PartnerPassword@2026",
        })
        assert login2_res.status_code == 200
        me2_res = await client.get("/api/v1/auth/me")
        assert me2_res.status_code == 200
        assert me2_res.json()["user"]["email"] == user2_email
        assert me2_res.json()["user"]["name"] == user2_name

        # Case 11: Test Google OAuth endpoint & guidance
        google_status = await client.get("/api/v1/auth/google/status")
        assert google_status.status_code == 200
        gs_data = google_status.json()
        assert "configured" in gs_data
        assert "instructions" in gs_data
        assert "redirect_uri" in gs_data
        assert "api/v1/auth/google/callback" in gs_data["redirect_uri"]

        # Case 12: Confirm the correct account name appears in the top-right profile
        assert me2_res.json()["user"]["display_name"] == user2_name


# ==============================================================================
# Task 2: Gmail Inbox Agent Tests
# ==============================================================================

def test_category_triage_billing_filters_design():
    """Verify classification into Billing, Filters, Design Settings, and others."""
    classifier = EmailClassifier()

    # Billing email
    e_billing = CustomerEmail(
        id="SIM-B1",
        sender_name="Alice",
        sender_email="alice@test.com",
        subject="Tax invoice statement and receipt",
        body="Can I get an updated vat receipt for our monthly subscription renewal?",
    )
    cl_billing = classifier.classify(e_billing)
    assert cl_billing.category == EmailCategory.BILLING

    # Filters email
    e_filters = CustomerEmail(
        id="SIM-F1",
        sender_name="Bob",
        sender_email="bob@test.com",
        subject="Sidebar dropdown filter customization",
        body="How can we configure custom facet search filters for member directories?",
    )
    cl_filters = classifier.classify(e_filters)
    assert cl_filters.category == EmailCategory.FILTERS

    # Design Settings email
    e_design = CustomerEmail(
        id="SIM-D1",
        sender_name="Carol",
        sender_email="carol@test.com",
        subject="Updating typography in design settings",
        body="We need to adjust brand color theme and fonts in the design settings panel.",
    )
    cl_design = classifier.classify(e_design)
    assert cl_design.category == EmailCategory.DESIGN_SETTINGS


def test_continuous_learning_loop(tmp_path):
    """Verify 5-minute learning loop captures reviewer edits and formats prompt injection."""
    db_file = str(tmp_path / "test_learning.db")
    engine = ContinuousLearningEngine(db_file)

    orig_draft = "Hello Bob,\n\nWe received your inquiry regarding filters.\n\nBest regards,\nSupport"
    user_edited = "Hi Bob,\n\nHere are the steps to update your sidebar filters:\n- Go to Settings\n- Click Search Results\n\nWarmest regards,\nBrilliant Directories Team"

    # Human reviewer edited draft and sent it
    rule = engine.learn_from_edit(
        category="filters",
        original_draft=orig_draft,
        user_edited_draft=user_edited,
        reviewer_name="Sarah Connor",
    )
    assert rule is not None
    assert rule["category"] == "filters"
    assert "Sign off with" in rule["learned_instruction"]

    # Active rules retrieved
    active = engine.get_learned_rules(category="filters")
    assert len(active) == 1

    # Formatted for subsequent prompt injection
    prompt_snippet = engine.format_rules_for_prompt(category="filters")
    assert "HUMAN REVIEWER PREFERENCES" in prompt_snippet
    assert "Sign off with" in prompt_snippet


def test_configurable_autonomy_streak():
    """Verify configurable consecutive approval streak threshold for autonomous sending."""
    autonomy = AutonomyEngine(streak_threshold=20)
    assert autonomy.streak_threshold == 20

    # User adjusts policy to 5 in settings modal
    autonomy.set_threshold(5)
    assert autonomy.streak_threshold == 5

    cat = EmailCategory.FILTERS
    # Approve 4 times without changes -> Streak = 4 (< 5, not authorized yet)
    for _ in range(4):
        autonomy.record_approval(category=cat, is_unchanged=True)
    can_auto4, _ = autonomy.can_send_autonomously(cat)
    assert can_auto4 is False

    # 5th approval reaches threshold -> Milestone achieved
    state5 = autonomy.record_approval(category=cat, is_unchanged=True)
    assert state5.consecutive_unchanged_count == 5


def test_gmail_client_simulator():
    """Verify Gmail simulator creates structured incoming emails for evaluators."""
    email_obj = GmailClient.generate_simulated_email(
        subject="Double charge on my statement",
        body="I see two identical charges for this billing cycle.",
        sender_name="David Miller",
        sender_email="david@client.com",
        category=EmailCategory.BILLING,
    )
    assert email_obj.id.startswith("SIM-")
    assert email_obj.sender_name == "David Miller"
    assert email_obj.category == EmailCategory.BILLING
