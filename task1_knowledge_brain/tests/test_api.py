"""End-to-End REST API Tests for Task 1: Knowledge Brain.

Uses isolated test fixtures (tmp_path) to ensure 100% hermetic tests that never collide
with live crawler data or across consecutive test runs.
"""

import uuid
import pytest
from fastapi.testclient import TestClient

from task1_knowledge_brain.app.crawler.runner import ResumableCrawler
from task1_knowledge_brain.app.crawler.state import CrawlStateStore
from task1_knowledge_brain.app.main import app
from task1_knowledge_brain.app.rag.engine import KnowledgeBrainEngine
from task1_knowledge_brain.app.rag.expert_store import ExpertFeedbackStore
from task1_knowledge_brain.app.rag.store import RAGVectorStore
from task1_knowledge_brain.app.rag.sync import KnowledgeBrainSynchronizer
import task1_knowledge_brain.app.api.routes as routes_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Hermetic test client fixture using temporary SQLite and ChromaDB paths."""
    test_db = str(tmp_path / "test_api_hermetic.db")
    test_chroma = str(tmp_path / "test_chroma_hermetic")

    isolated_state_store = CrawlStateStore(test_db)
    isolated_vector_store = RAGVectorStore(test_chroma)
    isolated_expert_store = ExpertFeedbackStore(test_db)
    isolated_crawler = ResumableCrawler(state_store=isolated_state_store)
    isolated_synchronizer = KnowledgeBrainSynchronizer(
        state_store=isolated_state_store,
        vector_store=isolated_vector_store,
    )
    isolated_engine = KnowledgeBrainEngine(
        vector_store=isolated_vector_store,
        expert_store=isolated_expert_store,
    )

    monkeypatch.setattr(routes_module, "_state_store", isolated_state_store)
    monkeypatch.setattr(routes_module, "_vector_store", isolated_vector_store)
    monkeypatch.setattr(routes_module, "_expert_store", isolated_expert_store)
    monkeypatch.setattr(routes_module, "_crawler", isolated_crawler)
    monkeypatch.setattr(routes_module, "_synchronizer", isolated_synchronizer)
    monkeypatch.setattr(routes_module, "_engine", isolated_engine)

    with TestClient(app) as test_client:
        yield test_client


def test_api_ui_html_response(client):
    """Verify GET / with text/html returns the dashboard UI."""
    response = client.get("/", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "AI Knowledge Brain" in response.text or "AI Tech Consultant" in response.text
    assert "Knowledge Assistant" in response.text


def test_api_status_endpoint(client):
    """Verify GET /api/v1/crawl/status returns counts and mode."""
    response = client.get("/api/v1/crawl/status")
    assert response.status_code == 200
    data = response.json()
    assert "crawled" in data
    assert "pending" in data
    assert "vector_chunks_count" in data
    assert data["is_mock_mode"] is True


def test_api_query_refusal_and_expert_approval_flow(client):
    """Verify that an unknown question triggers refusal and can be resolved by an expert."""
    unique_tag = uuid.uuid4().hex[:8]
    question = f"How do I configure quantum subspace engine {unique_tag}?"

    # 1. Ask unknown question in isolated DB -> expect refusal
    res1 = client.post("/api/v1/query", json={"question": question, "top_k": 3})
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["is_sufficient"] is False
    assert data1["needs_human_review"] is True
    assert "insufficient evidence" in data1["answer"].lower()

    # 2. Check that it appears in unanswered queue
    unans_res = client.get("/api/v1/feedback/unanswered")
    assert unans_res.status_code == 200
    unans = unans_res.json()
    assert any(question in q["question"] for q in unans)

    # 3. Approve expert answer
    approve_res = client.post(
        "/api/v1/feedback/approve",
        json={
            "question": question,
            "answer": f"Subspace engine {unique_tag} is fictional. Brilliant Directories is an enterprise directory CMS.",
            "expert_name": "Chief Systems Architect",
        },
    )
    assert approve_res.status_code == 200
    appr_data = approve_res.json()
    assert appr_data["status"] == "approved"

    # 4. Ask the question again -> expect verified human answer
    res2 = client.post("/api/v1/query", json={"question": question, "top_k": 3})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["is_sufficient"] is True
    assert "Verified Expert Solution" in data2["answer"]
    assert "Chief Systems Architect" in data2["answer"]


def test_api_list_articles(client):
    """Verify GET /api/v1/articles returns paginated results."""
    response = client.get("/api/v1/articles?limit=5&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert "articles" in data
    assert "limit" in data
    assert data["limit"] == 5


def test_api_batch_crawl_limit_capped(client):
    """Verify that batch crawl enforces <= 10 articles per batch."""
    # Attempting batch_size > 10 should either be capped or validated
    response = client.post("/api/v1/crawl/batch", json={"batch_size": 25})
    # Since le=10 is defined on the Pydantic schema, it should reject or cap
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        data = response.json()
        assert data["crawl_result"]["processed"] <= 10


def test_api_grounded_payment_gateways_query(client):
    """Priority 1: Verify payment gateways query returns grounded answer with citations."""
    from task1_knowledge_brain.app.rag.chunker import DocumentChunker
    import task1_knowledge_brain.app.api.routes as r

    url = "https://support.brilliantdirectories.com/support/solutions/articles/12000006843-supported-payment-gateways"
    chunker = DocumentChunker()
    chunks = chunker.chunk_article(
        url=url,
        title="Supported Payment Gateways",
        breadcrumbs="Finance > Payment Settings",
        body_text="Brilliant Directories supports Stripe, PayPal Standard, PayPal Payflow Pro, Authorize.net, and eWAY for member signups.",
        images=[],
    )
    r._vector_store.add_article_chunks(url, chunks)

    response = client.post(
        "/api/v1/query",
        json={"question": "How to configure payment gateways for member signups?", "top_k": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["is_grounded"] is True
    assert data["is_sufficient"] is True
    assert len(data["sources"]) > 0
    assert any("Payment Gateway" in s["title"] for s in data["sources"])
    assert "Payment Gateway" in data["answer"]


def test_api_health_dependencies(client):
    """Priority 2: Verify health check reports database and vector store connectivity."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database_connected"] is True
    assert data["vector_store_healthy"] is True
    assert data["is_mock_mode"] is True


def test_api_actionable_webhooks_query(client):
    """Verify webhooks query returns actionable steps, deduplicated citations, and mock mode label."""
    from task1_knowledge_brain.app.rag.chunker import DocumentChunker
    import task1_knowledge_brain.app.api.routes as r

    chunker = DocumentChunker()
    wh_url = "https://support.brilliantdirectories.com/support/solutions/articles/12000083005-developer-hub-webhooks"
    wh_text = (
        "Developer Hub - Webhooks\n"
        "Opening the Webhooks Page:\n"
        "Go to Developer Hub. On the Manage Webhooks card, click Manage Webhooks.\n"
        "Turning a Webhook On:\n"
        "1. Switch the webhook on.\n"
        "2. Paste the endpoint address into its Webhook Link (must start with https:// or http://).\n"
        "3. Click Save in the Unsaved changes bar at the top of the page.\n"
        "Default Webhook Events include Get Matched Lead Form Submitted, Contact Us, and Member Signups."
    )
    zapier_url = "https://support.brilliantdirectories.com/support/solutions/articles/12000082977-connecting-webhook-events-with-zapier"
    zapier_text = (
        "Connecting Webhook Events with Zapier\n"
        "Copy the Custom Webhook URL from Zapier Catch Hook and paste into Webhook Link."
    )

    chunks1 = chunker.chunk_article(wh_url, "Developer Hub - Webhooks", "Developer Hub > Webhooks", wh_text, [])
    chunks2 = chunker.chunk_article(zapier_url, "Connecting Webhook Events with Zapier", "Developer Hub > Webhooks", zapier_text, [])
    r._vector_store.add_article_chunks(wh_url, chunks1)
    r._vector_store.add_article_chunks(zapier_url, chunks2)

    response = client.post(
        "/api/v1/query",
        json={"question": "How to set up webhooks for real-time notifications?", "top_k": 8},
    )
    assert response.status_code == 200
    data = response.json()

    # Grounding & sufficiency
    assert data["is_grounded"] is True
    assert data["is_sufficient"] is True

    # Mode distinction
    assert data["is_mock"] is True
    assert "mock" in data["model_name"].lower()
    assert "claude" not in data["model_name"].lower()  # Must not claim to be Claude

    # Latency measurement
    assert data["latency_ms"] > 0
    assert data["confidence_score"] >= 0.50

    # Actionable procedural steps in answer
    ans = data["answer"]
    assert "Developer Hub" in ans
    assert "Manage Webhooks" in ans
    assert "Webhook Link" in ans
    assert "Save" in ans
    assert "Actionable Setup Steps" in ans

    # Exact citations with markdown links
    assert f"[{wh_url}]" in ans or f"[Developer Hub - Webhooks]({wh_url})" in ans

    # Citation deduplication: verify no duplicate URLs in sources
    source_urls = [s["url"] for s in data["sources"]]
    assert len(source_urls) == len(set(source_urls)), "Sources must not contain duplicate URLs"


def test_api_lead_distribution_query(client):
    """Verify lead distribution query returns automatic matching details."""
    from task1_knowledge_brain.app.rag.chunker import DocumentChunker
    import task1_knowledge_brain.app.api.routes as r

    url = "https://support.brilliantdirectories.com/support/solutions/articles/12000007994-what-is-the-automatic-lead-matching-add-on-"
    title = "Lead Distribution - What Is The Automatic Lead Matching Add-On?"
    text = (
        "Lead Distribution - What Is The Automatic Lead Matching Add-On?\n"
        "How the lead distribution system works:\n"
        "All directory websites generate leads from visitors who contact professional members. "
        "This add-on allows the site to instantly send leads to members without manually matching them.\n"
        "By default, leads submitted through the 'Get Matched' form are marked as Pending and require manual review by an admin. "
        "With the auto-matching feature enabled, the lead distribution system automatically matches leads based on request details."
    )
    chunker = DocumentChunker()
    chunks = chunker.chunk_article(url, title, "Leads", text, [])
    r._vector_store.add_article_chunks(url, chunks)

    response = client.post(
        "/api/v1/query",
        json={"question": "How does the lead distribution system work?", "top_k": 5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["is_grounded"] is True
    assert data["is_sufficient"] is True
    assert "Automatic Lead Matching" in data["answer"]
    assert len(data["sources"]) > 0


def test_api_dashboard_counts_reflection(client):
    """Verify dashboard status endpoint reflects real SQLite records and ChromaDB chunk count."""
    import task1_knowledge_brain.app.api.routes as r

    status_res = client.get("/api/v1/crawl/status")
    assert status_res.status_code == 200
    data = status_res.json()

    assert "crawled" in data
    assert "pending" in data
    assert "vector_chunks_count" in data
    assert "unanswered_count" in data
    assert data["vector_chunks_count"] == r._vector_store.count()
    assert data["unanswered_count"] == r._expert_store.count_pending_questions()


