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


HEALTHHUB_SAMPLE_1 = """John (Client): We are building a directory for certified physical therapists. We need a way to accept membership payments through Stripe.
Sarah (Consultant): Does Brilliant Directories support Stripe natively?
John: Yes, we want members to pay monthly. We also need webhooks to notify our marketing software.
John: Then we need a custom bidirectional integration with our internal patient scheduling database.
John: We must launch within 4 weeks, and our budget is around $8,000.
John: Also, make sure the search is really fast and has some sort of modern design."""

HEALTHHUB_SAMPLE_2 = """John (Client): Hi Sarah, we had an internal board meeting and updated our requirements.
John: We still need Stripe payment processing and webhooks.
John: In addition to the patient scheduling database, we now need a custom native mobile app for iOS and Android.
John: We also need full multi-currency billing supporting Euros and British Pounds with dynamic exchange rate recalculation.
John: And we dropped the fast search requirement for now."""


def test_healthhub_all_six_requirements_covered():
    """Verify explicit tracking of all 6 HealthHub Directory requirements with concise actionable titles."""
    extractor = TranscriptExtractorStage()
    analysis = extractor.extract(HEALTHHUB_SAMPLE_1, client_name="HealthHub Directories")

    # 1. Check requirement count
    assert len(analysis.requirements) == 6, f"Expected 6 requirements, got {len(analysis.requirements)}"

    # 2. Check canonical keys for all 6 requirements
    keys = {r.canonical_key for r in analysis.requirements}
    expected_keys = {
        "directory_setup",
        "stripe_payments",
        "webhooks",
        "patient_database_integration",
        "fast_search",
        "modern_design",
    }
    assert expected_keys.issubset(keys), f"Missing keys: {expected_keys - keys}"

    # 3. Check concise, actionable titles (not raw transcript statements)
    for req in analysis.requirements:
        assert not req.title.lower().startswith("john:"), f"Title contains speaker prefix: {req.title}"
        assert not req.title.lower().startswith("also, make sure"), f"Title is raw transcript sentence: {req.title}"
        assert len(req.title.split()) <= 10, f"Title too verbose: {req.title}"

    # 4. Check ambiguity questions contain 'criteria'
    modern_req = next(r for r in analysis.requirements if r.canonical_key == "modern_design")
    assert modern_req.is_ambiguous is True
    assert "criteria" in modern_req.clarification_question.lower()

    search_req = next(r for r in analysis.requirements if r.canonical_key == "fast_search")
    assert search_req.is_ambiguous is True
    assert "criteria" in search_req.clarification_question.lower()


@pytest.mark.asyncio
async def test_native_feature_citations_and_custom_classification():
    """Verify native Brilliant Directories citations and custom dev classifications."""
    extractor = TranscriptExtractorStage()
    checker = NativeFeatureCheckerStage()

    analysis = extractor.extract(HEALTHHUB_SAMPLE_1, client_name="HealthHub Directories")
    verified = await checker.verify_requirements(analysis)

    # Native features must have citations
    native_keys = ["stripe_payments", "webhooks", "directory_setup", "fast_search", "modern_design"]
    for k in native_keys:
        req = next((r for r in verified.requirements if r.canonical_key == k), None)
        assert req is not None, f"Missing {k}"
        assert req.is_native_feature is True
        assert len(req.source_citations) >= 1
        assert "http" in req.source_citations[0]["url"]

    # Custom integration must NOT be native and requires ~24h
    custom_req = next(r for r in verified.requirements if r.canonical_key == "patient_database_integration")
    assert custom_req.is_native_feature is False
    assert custom_req.estimated_hours >= 16.0


def test_qa_math_cost_consistency_and_deduplication():
    """Verify strict 15% QA calculation, exact table/summary sums, and deduplication."""
    extractor = TranscriptExtractorStage()
    estimator = ScopeEstimatorStage(hourly_rate=120.0)

    # Transcript with repeated Stripe mentions across turns
    repeated_transcript = """
    John: We need Stripe payment processing.
    Sarah: Understood.
    John: Yes, Stripe payments for monthly memberships are essential.
    John: We also need webhooks.
    """
    analysis = extractor.extract(repeated_transcript, client_name="HealthHub Directories")
    sow = estimator.estimate(analysis)

    # Check deduplication: stripe should only have 1 work package task
    stripe_tasks = [t for t in sow.tasks if "stripe" in t.title.lower()]
    assert len(stripe_tasks) == 1, "Duplicate task generated for repeated requirement"

    # Check missing requirements: all should be accounted for
    assert sow.unaccounted_requirements == []

    # Check 15% QA calculation
    dev_tasks = [t for t in sow.tasks if t.work_type != "qa_testing"]
    qa_task = next((t for t in sow.tasks if t.work_type == "qa_testing"), None)
    assert qa_task is not None

    dev_hours = sum(t.developer_hours for t in dev_tasks)
    expected_qa_hours = round(dev_hours * 0.15, 1)
    assert qa_task.developer_hours == expected_qa_hours
    assert qa_task.estimated_cost == round(expected_qa_hours * 120.0, 2)

    # Check exact sum consistency between table tasks and summary totals
    assert sow.total_developer_hours == round(sum(t.developer_hours for t in sow.tasks), 1)
    assert sow.total_cost_usd == round(sum(t.estimated_cost for t in sow.tasks), 2)
    assert sow.total_cost_usd == round(sow.total_developer_hours * 120.0, 2)


@pytest.mark.asyncio
async def test_dynamic_scope_creep_detection_sample1_to_sample2():
    """Verify dynamic baseline vs revision comparison without hardcoded delta values."""
    pipeline = MeetingToScopePipeline()

    p1, _ = await pipeline.process_transcript(HEALTHHUB_SAMPLE_1, client_name="HealthHub Directories")
    p2, diff2 = await pipeline.process_transcript(
        HEALTHHUB_SAMPLE_2, client_name="HealthHub Directories", previous_meeting_id=p1.meeting_id
    )

    assert diff2 is not None
    assert diff2.previous_version == 1
    assert diff2.new_version == 2

    # Added features: Mobile app and Multi-currency billing
    assert any("mobile app" in a.lower() for a in diff2.added_requirements)
    assert any("multi-currency" in a.lower() for a in diff2.added_requirements)

    # Removed feature: Fast search
    assert any("search" in r.lower() for r in diff2.removed_requirements)

    # Dynamic hour and cost deltas
    assert diff2.hours_delta > 0
    assert diff2.cost_delta > 0
    assert diff2.cost_delta == round(p2.scope_of_work.total_cost_usd - p1.scope_of_work.total_cost_usd, 2)

    # Scope creep alerts triggered
    assert len(diff2.scope_creep_warnings) >= 1
    assert any("Budget surge" in w or "Scope expansion" in w for w in diff2.scope_creep_warnings)


@pytest.mark.asyncio
async def test_human_signoff_boundary_and_export_persistence():
    """Verify proposal stays unapproved until explicit human sign-off, and state persists across export."""
    with TestClient(app) as client:
        # 1. Process HealthHub Sample 1
        res = client.post(
            "/api/v1/scope/process",
            json={"transcript_text": HEALTHHUB_SAMPLE_1, "client_name": "HealthHub Directories"},
        )
        assert res.status_code == 200
        data = res.json()
        meeting_id = data["proposal"]["meeting_id"]

        # Default boundary: Must await human sign-off
        assert data["proposal"]["is_approved_by_human"] is False
        assert data["proposal"]["approved_by"] is None

        # Export before sign-off: Must indicate AWAITING HUMAN SIGN-OFF
        res_exp_pre = client.get(f"/api/v1/scope/{meeting_id}/export")
        assert res_exp_pre.status_code == 200
        assert "AWAITING HUMAN SIGN-OFF (Draft)" in res_exp_pre.text

        # 2. Human consultant performs approval
        res_appr = client.post(
            f"/api/v1/scope/{meeting_id}/approve",
            json={"approver_name": "Lead Solutions Architect"},
        )
        assert res_appr.status_code == 200
        appr_data = res_appr.json()
        assert appr_data["is_approved_by_human"] is True
        assert appr_data["approved_by"] == "Lead Solutions Architect"
        assert appr_data["approved_at"] is not None

        # 3. Export after sign-off: Must reflect APPROVED status and consultant name
        res_exp_post = client.get(f"/api/v1/scope/{meeting_id}/export")
        assert res_exp_post.status_code == 200
        assert "APPROVED (Signed off by Lead Solutions Architect" in res_exp_post.text
        assert "Verified Source Documentation" in res_exp_post.text


def test_pdf_export_endpoint_and_validity():
    """Verify PDF export endpoint generates downloadable, valid, publication-grade PDF matching SOW totals."""
    with TestClient(app) as client:
        # Process HealthHub Sample 1
        res = client.post(
            "/api/v1/scope/process",
            json={"transcript_text": HEALTHHUB_SAMPLE_1, "client_name": "HealthHub Directories"},
        )
        assert res.status_code == 200
        meeting_id = res.json()["proposal"]["meeting_id"]

        # 1. Download PDF export (Draft state)
        pdf_res = client.get(f"/api/v1/scope/{meeting_id}/export/pdf")
        assert pdf_res.status_code == 200
        assert pdf_res.headers["content-type"] == "application/pdf"
        assert f"SOW_HealthHub_Directories_{meeting_id}_v1.pdf" in pdf_res.headers.get("content-disposition", "")

        pdf_bytes = pdf_res.content
        assert len(pdf_bytes) > 2000
        assert pdf_bytes.startswith(b"%PDF-"), "Invalid PDF signature"

        text_content = pdf_bytes.decode("latin1", errors="ignore")
        # Assert required fields in draft state
        assert "HealthHub Directories" in text_content
        assert "DRAFT" in text_content
        assert "AWAITING HUMAN SIGN-OFF" in text_content
        assert "5,376.00" in text_content  # Exact matching cost
        assert "44.8h" in text_content or "44.8" in text_content  # Exact matching hours
        assert "support.brilliantdirectories.com" in text_content  # Clickable documentation URL


def test_pdf_export_approved_status():
    """Verify PDF export updates to reflect approval status, consultant name, and timestamp."""
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/scope/process",
            json={"transcript_text": HEALTHHUB_SAMPLE_1, "client_name": "HealthHub Directories"},
        )
        assert res.status_code == 200
        meeting_id = res.json()["proposal"]["meeting_id"]

        # Human consultant approves
        appr_res = client.post(
            f"/api/v1/scope/{meeting_id}/approve",
            json={"approver_name": "Lead Solutions Architect"},
        )
        assert appr_res.status_code == 200

        # Download PDF export (Approved state)
        pdf_res = client.get(f"/api/v1/scope/{meeting_id}/export/pdf")
        assert pdf_res.status_code == 200
        pdf_bytes = pdf_res.content
        text_content = pdf_bytes.decode("latin1", errors="ignore")

        assert "OFFICIALLY APPROVED" in text_content
        assert "Lead Solutions Architect" in text_content


def test_pdf_export_revision_diff():
    """Verify PDF export includes revision comparison table, deltas, and scope creep warnings."""
    with TestClient(app) as client:
        # V1
        res1 = client.post(
            "/api/v1/scope/process",
            json={"transcript_text": HEALTHHUB_SAMPLE_1, "client_name": "HealthHub Directories"},
        )
        meeting_id = res1.json()["proposal"]["meeting_id"]

        # V2 Revision
        res2 = client.post(
            "/api/v1/scope/process",
            json={
                "transcript_text": HEALTHHUB_SAMPLE_2,
                "client_name": "HealthHub Directories",
                "previous_meeting_id": meeting_id,
            },
        )
        assert res2.status_code == 200

        # Download V2 PDF
        pdf_res = client.get(f"/api/v1/scope/{meeting_id}/export/pdf")
        assert pdf_res.status_code == 200
        pdf_bytes = pdf_res.content
        text_content = pdf_bytes.decode("latin1", errors="ignore")

        assert "Revision Comparison" in text_content
        assert "Mobile App" in text_content or "mobile app" in text_content.lower()
        assert "Scope Creep Warnings" in text_content or "Budget surge" in text_content


def test_pdf_export_404_not_found():
    """Verify PDF export returns 404 for unknown meeting ID."""
    with TestClient(app) as client:
        res = client.get("/api/v1/scope/MTG-NONEXISTENT/export/pdf")
        assert res.status_code == 404


