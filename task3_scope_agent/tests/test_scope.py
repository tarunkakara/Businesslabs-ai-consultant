"""Automated Unit and Integration Tests for Task 3: Meeting-to-Scope Agent."""

import pytest
from fastapi.testclient import TestClient

from task3_scope_agent.app.main import app
from task3_scope_agent.app.models import RequirementType
from task3_scope_agent.app.pipeline import MeetingToScopePipeline
from task3_scope_agent.app.stages.diff_stage import TranscriptDiffStage
from task3_scope_agent.app.stages.estimator_stage import ScopeEstimatorStage
from task3_scope_agent.app.stages.extractor_stage import TranscriptExtractorStage
from task3_scope_agent.app.stages.feature_checker import NativeFeatureCheckerStage
from task3_scope_agent.app.stages.proposal_stage import ProposalGeneratorStage


SAMPLE_TRANSCRIPT_V1 = """
Sarah (Consultant): Welcome John. Let's discuss your requirements for the therapist directory.
John (Client): We need to accept member payments via Stripe.
John: We also want webhooks to notify our CRM on signup.
John: We need a custom bidirectional integration with our patient booking database.
John: We must launch within 4 weeks. Our budget is around $8,000.
John: Make sure the search has some sort of modern look.
"""

SAMPLE_TRANSCRIPT_V2 = """
Sarah: Welcome back John. What updates do we have?
John: We still need Stripe payments and webhooks.
John: We still need the patient booking database integration.
John: In addition, we now need a native mobile app for iOS and Android.
John: We also require multi-currency billing supporting Euros and AED.
"""


def test_transcript_extractor_stage():
    """Verify extraction of requirements, constraints, dependencies, and ambiguous items."""
    extractor = TranscriptExtractorStage()
    analysis = extractor.extract(SAMPLE_TRANSCRIPT_V1, client_name="TherapyHub")

    assert len(analysis.requirements) >= 3
    assert len(analysis.constraints) >= 1
    assert any("4 weeks" in c or "8,000" in c for c in analysis.constraints)

    # Ambiguity check on 'some sort of modern look'
    vague_reqs = [r for r in analysis.requirements if r.is_ambiguous]
    assert len(vague_reqs) >= 1
    assert vague_reqs[0].clarification_question is not None


@pytest.mark.asyncio
async def test_native_feature_checker_stage():
    """Requirement: Check Knowledge Brain API for existing features before recommending custom dev."""
    extractor = TranscriptExtractorStage()
    checker = NativeFeatureCheckerStage()

    analysis = extractor.extract(SAMPLE_TRANSCRIPT_V1, client_name="TherapyHub")
    verified = await checker.verify_requirements(analysis)

    stripe_req = next((r for r in verified.requirements if "stripe" in (r.title + " " + r.description).lower()), None)
    assert stripe_req is not None
    assert stripe_req.is_native_feature is True
    assert stripe_req.estimated_hours <= 4.0  # Configuration only

    custom_req = next((r for r in verified.requirements if "patient booking" in r.description.lower()), None)
    assert custom_req is not None
    assert custom_req.is_native_feature is False
    assert custom_req.estimated_hours >= 16.0  # Custom dev required


def test_scope_estimator_stage():
    """Verify work package breakdown, hours calculation, QA allocation, and rate math."""
    extractor = TranscriptExtractorStage()
    estimator = ScopeEstimatorStage(hourly_rate=120.0)

    analysis = extractor.extract(SAMPLE_TRANSCRIPT_V1, client_name="TherapyHub")
    # Mark first as native, second as custom
    analysis.requirements[0].is_native_feature = True
    analysis.requirements[0].estimated_hours = 3.0
    if len(analysis.requirements) > 1:
        analysis.requirements[1].is_native_feature = False
        analysis.requirements[1].estimated_hours = 24.0

    sow = estimator.estimate(analysis)

    assert sow.total_developer_hours > 0
    assert sow.total_cost_usd == sow.total_developer_hours * 120.0
    assert any(t.work_type == "qa_testing" for t in sow.tasks)
    assert len(sow.native_features_utilized) >= 1


def test_proposal_stage_human_approval_boundary():
    """Requirement: Require human approval before finalizing a client-facing proposal."""
    extractor = TranscriptExtractorStage()
    estimator = ScopeEstimatorStage()
    gen = ProposalGeneratorStage()

    analysis = extractor.extract(SAMPLE_TRANSCRIPT_V1, client_name="TherapyHub")
    sow = estimator.estimate(analysis)

    proposal = gen.generate(analysis, sow)
    # Human approval must default to False
    assert proposal.is_approved_by_human is False
    assert proposal.approved_by is None

    # Human consultant approves
    approved = gen.approve_proposal(proposal, approver_name="Senior Architect")
    assert approved.is_approved_by_human is True
    assert approved.approved_by == "Senior Architect"
    assert approved.approved_at is not None


def test_transcript_diff_stage_and_scope_creep():
    """Requirement: Accept revised transcript, show additions/removals, and detect scope creep."""
    extractor = TranscriptExtractorStage()
    estimator = ScopeEstimatorStage()
    diff_engine = TranscriptDiffStage()

    # V1
    a1 = extractor.extract(SAMPLE_TRANSCRIPT_V1, client_name="TherapyHub", version=1)
    sow1 = estimator.estimate(a1)

    # V2
    a2 = extractor.extract(SAMPLE_TRANSCRIPT_V2, client_name="TherapyHub", version=2)
    sow2 = estimator.estimate(a2)

    diff = diff_engine.compare(a1, sow1, a2, sow2)

    assert diff.previous_version == 1
    assert diff.new_version == 2
    assert len(diff.added_requirements) >= 1
    assert len(diff.scope_creep_warnings) >= 1
    assert "Revision Comparison" in diff.diff_summary_markdown


@pytest.mark.asyncio
async def test_meeting_to_scope_api_pipeline():
    """Verify E2E FastAPI endpoints for transcript processing, revision, approval, and export."""
    with TestClient(app) as client:
        # 1. Health
        res_h = client.get("/health")
        assert res_h.status_code == 200

        # 2. Process V1 transcript
        res_p1 = client.post(
            "/api/v1/scope/process",
            json={
                "transcript_text": SAMPLE_TRANSCRIPT_V1,
                "client_name": "TherapyHub Inc",
            },
        )
        assert res_p1.status_code == 200
        data1 = res_p1.json()
        meeting_id = data1["proposal"]["meeting_id"]
        assert data1["proposal"]["is_approved_by_human"] is False

        # 3. Process V2 revised transcript
        res_p2 = client.post(
            "/api/v1/scope/process",
            json={
                "transcript_text": SAMPLE_TRANSCRIPT_V2,
                "client_name": "TherapyHub Inc",
                "previous_meeting_id": meeting_id,
            },
        )
        assert res_p2.status_code == 200
        data2 = res_p2.json()
        assert data2["proposal"]["version"] == 2
        assert data2["diff"] is not None
        assert len(data2["diff"]["added_requirements"]) >= 1

        # 4. Human Approval
        res_appr = client.post(
            f"/api/v1/scope/{meeting_id}/approve",
            json={"approver_name": "Principal Consultant"},
        )
        assert res_appr.status_code == 200
        assert res_appr.json()["is_approved_by_human"] is True

        # 5. Export Markdown
        res_exp = client.get(f"/api/v1/scope/{meeting_id}/export")
        assert res_exp.status_code == 200
        assert "Technical Implementation Proposal" in res_exp.text
        assert "Work Package Breakdown" in res_exp.text
