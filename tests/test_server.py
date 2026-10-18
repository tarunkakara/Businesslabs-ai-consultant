"""Integration tests for the unified server gateway."""

import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from server import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_portal_html_response(client):
    """GET / should serve the Suite Portal HTML."""
    res = client.get("/", headers={"Accept": "text/html"})
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "AI Tech Consultant" in res.text
    assert "Knowledge Brain" in res.text
    assert "Inbox Agent" in res.text
    assert "Scope Agent" in res.text


def test_portal_json_registry(client):
    """GET / with JSON accept header should return the API and service registry."""
    res = client.get("/", headers={"Accept": "application/json"})
    assert res.status_code == 200
    data = res.json()
    assert data["service"] == "BusinessLabs AI Tech Consultant Monorepo"
    assert "task1_knowledge_brain" in data["services"]
    assert "task2_inbox_agent" in data["services"]
    assert "task3_scope_agent" in data["services"]


def test_task_views_html(client):
    """All 3 task routes (/brain, /inbox, /scope) must serve their respective web UIs."""
    res1 = client.get("/brain")
    assert res1.status_code == 200
    assert "text/html" in res1.headers["content-type"]
    assert "Knowledge Brain" in res1.text

    res2 = client.get("/inbox")
    assert res2.status_code == 200
    assert "text/html" in res2.headers["content-type"]
    assert "Inbox Agent" in res2.text

    res3 = client.get("/scope")
    assert res3.status_code == 200
    assert "text/html" in res3.headers["content-type"]
    assert "Scope Agent" in res3.text


def test_unified_health_endpoint(client):
    """GET /health must aggregate telemetry across all 3 microservices."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ["ok", "degraded"]
    assert "task1_knowledge_brain" in data
    assert "task2_inbox_agent" in data
    assert "task3_scope_agent" in data
    assert data["task1_knowledge_brain"]["database_connected"] is True
    assert data["task1_knowledge_brain"]["vector_store_healthy"] is True
    assert data["task2_inbox_agent"]["emails_count"] > 0
    assert data["task3_scope_agent"]["require_human_sign_off"] is True


def test_unified_routes_dispatch(client):
    """Verify endpoints from all 3 tasks operate seamlessly under the single gateway."""
    # Task 1: Knowledge Brain query
    q_res = client.post("/api/v1/query", json={"question": "What payment gateways are supported?", "top_k": 3})
    assert q_res.status_code == 200
    assert "answer" in q_res.json()

    # Task 2: Inbox Agent emails
    emails_res = client.get("/api/v1/emails?limit=5")
    assert emails_res.status_code == 200
    emails = emails_res.json()
    assert len(emails) >= 1

    # Task 3: Scope Agent process
    transcript = "John: We need to accept member payments via Stripe. We also want webhooks to notify our CRM."
    scope_res = client.post("/api/v1/scope/process", json={"transcript_text": transcript, "client_name": "TestCorp"})
    assert scope_res.status_code == 200
    proposal = scope_res.json()["proposal"]
    assert proposal["client_name"] == "TestCorp"
    assert len(proposal["scope_of_work"]["tasks"]) >= 2
