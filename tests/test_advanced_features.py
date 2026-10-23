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
async def test_user_authentication_api():
    """Verify login page, session authentication, email normalization, password rules, and profile endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Login page HTML serves successfully
        login_res = await client.get("/login")
        assert login_res.status_code == 200
        assert "Sign In" in login_res.text
        assert "Create Account" in login_res.text

        # 2. Reject invalid login credentials
        bad_res = await client.post("/api/v1/auth/login", json={"email": "lead@businesslabs.ai", "password": "WrongPassword!1"})
        assert bad_res.status_code == 401

        # 3. Successful login with lowercase email normalization and valid password
        auth_res = await client.post("/api/v1/auth/login", json={"email": "LEAD@BUSINESSLABS.AI", "password": "Consultant@123"})
        assert auth_res.status_code == 200
        data = auth_res.json()
        assert data["status"] == "success"
        assert data["user"]["email"] == "lead@businesslabs.ai"
        assert "session_token" in auth_res.cookies or "session_user" in auth_res.cookies

        # 4. Auth me endpoint returns user profile
        me_res = await client.get("/api/v1/auth/me", cookies=auth_res.cookies)
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data["user"]["email"] == "lead@businesslabs.ai"

        # 5. Registration with password complexity enforcement
        # Fails without special char or uppercase or length
        weak_reg = await client.post("/api/v1/auth/register", json={
            "email": "newuser@businesslabs.ai",
            "password": "simplepassword",
            "full_name": "Test User"
        })
        assert weak_reg.status_code == 400
        assert "special character" in weak_reg.json()["detail"].lower() or "uppercase" in weak_reg.json()["detail"].lower()

        # Succeeds with compliant password
        unique_email = f"user_{secrets.token_hex(4)}@businesslabs.ai"
        good_reg = await client.post("/api/v1/auth/register", json={
            "email": unique_email.upper(),  # Verify case normalization during registration
            "password": "SecurePassword@2026",
            "full_name": "Test User"
        })
        assert good_reg.status_code == 200
        assert good_reg.json()["user"]["email"] == unique_email.lower()

        # 6. Logout clears session
        logout_res = await client.post("/api/v1/auth/logout")
        assert logout_res.status_code == 200


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
