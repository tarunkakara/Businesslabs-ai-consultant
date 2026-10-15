"""End-to-End REST API Tests for Task 1: Knowledge Brain."""

import uuid
import pytest
from fastapi.testclient import TestClient

from task1_knowledge_brain.app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_api_ui_html_response(client):
    """Verify GET / with text/html returns the dashboard UI."""
    response = client.get("/", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "BusinessLabs — AI Knowledge Brain" in response.text
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
    question = f"How do I configure subspace warp engine {unique_tag} on Brilliant Directories?"

    # 1. Ask unknown question -> expect refusal
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
            "answer": f"Warp engines {unique_tag} are science fiction. Brilliant Directories is an enterprise directory CMS.",
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
