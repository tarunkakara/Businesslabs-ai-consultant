"""Automated unit and integration tests for Task 1 Health, Config, and Mock Provider."""

import pytest
from fastapi.testclient import TestClient

from task1_knowledge_brain.app.config import Settings
from task1_knowledge_brain.app.main import app
from task1_knowledge_brain.app.providers.mock_provider import MockClaudeProvider, MockVisionProvider


@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    with TestClient(app) as test_client:
        yield test_client


def test_settings_load():
    """Verify that configuration settings load with expected defaults."""
    cfg = Settings(provider_mode="mock")
    assert cfg.knowledge_brain_port == 8000
    assert cfg.crawl_sitemap_url == "https://support.brilliantdirectories.com/support/sitemap.xml"
    assert cfg.is_mock_mode is True


def test_health_endpoint(client):
    """Verify /health returns HTTP 200 and valid JSON schema."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "task1_knowledge_brain"
    assert data["version"] == "1.0.0"
    assert data["provider_mode"] == "mock"
    assert data["is_mock_mode"] is True
    assert "mock" in data["active_model"].lower()


def test_root_endpoint(client):
    """Verify root endpoint exposes documentation and service metadata."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["docs_url"] == "/docs"
    assert data["status"] == "online"


@pytest.mark.asyncio
async def test_mock_provider_generation():
    """Verify MockClaudeProvider returns structured LLMResponse with token counts."""
    mock = MockClaudeProvider()
    res = await mock.generate(prompt="Hello, test consultant!")
    assert res.is_mock is True
    assert res.usage.prompt_tokens > 0
    assert res.usage.completion_tokens > 0
    assert res.latency_ms >= 0


@pytest.mark.asyncio
async def test_mock_provider_refusal_when_insufficient_evidence():
    """Requirement: Clearly state when the available evidence is insufficient instead of inventing answers."""
    mock = MockClaudeProvider()
    result = await mock.answer_grounded(
        question="What is the quantum encryption algorithm used by Brilliant Directories?",
        retrieved_contexts=[],  # Empty context -> must refuse
    )
    assert result.is_sufficient is False
    assert result.needs_human_review is True
    assert "insufficient evidence" in result.answer.lower()
    assert len(result.sources) == 0


@pytest.mark.asyncio
async def test_mock_provider_grounded_answer_with_citations():
    """Requirement: Answer user questions with source citations linking to relevant articles."""
    mock = MockClaudeProvider()
    mock_context = [
        {
            "title": "How to Configure Payment Gateways",
            "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000000001",
            "text": "Navigate to Finance -> Payment Settings to configure Stripe or PayPal credentials.",
            "similarity_score": 0.88,
        }
    ]
    result = await mock.answer_grounded(
        question="Where do I configure Stripe?",
        retrieved_contexts=mock_context,
    )
    assert result.is_sufficient is True
    assert result.needs_human_review is False
    assert "[How to Configure Payment Gateways]" in result.answer
    assert "https://support.brilliantdirectories.com/support/solutions/articles/12000000001" in result.answer
    assert len(result.sources) == 1


@pytest.mark.asyncio
async def test_mock_vision_provider():
    """Verify MockVisionProvider produces structured visual captions."""
    vision = MockVisionProvider()
    desc = await vision.describe_image(
        image_url="https://support.brilliantdirectories.com/assets/stripe-setup.png",
        article_title="Stripe Setup Guide",
        alt_text="Stripe API keys configuration screen",
    )
    assert "Stripe API keys configuration screen" in desc
    assert "Stripe Setup Guide" in desc
