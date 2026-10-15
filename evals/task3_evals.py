"""Automated Evaluation Suite for Task 3: Meeting-to-Scope Agent.

Runs 20 rigorous test cases across transcript extraction, ambiguity detection,
Knowledge Brain native feature verification, custom development hours estimation,
long transcript resilience, revision diffing, scope creep alerts, and human sign-off.
"""

import asyncio
import json
import time
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from task3_scope_agent.app.config import settings
from task3_scope_agent.app.models import RequirementType
from task3_scope_agent.app.pipeline import MeetingToScopePipeline
from task3_scope_agent.app.stages.diff_stage import TranscriptDiffStage
from task3_scope_agent.app.stages.estimator_stage import ScopeEstimatorStage
from task3_scope_agent.app.stages.extractor_stage import TranscriptExtractorStage
from task3_scope_agent.app.stages.feature_checker import NativeFeatureCheckerStage
from task3_scope_agent.app.stages.proposal_stage import ProposalGeneratorStage


async def run_task3_evaluations() -> List[Dict[str, Any]]:
    """Execute all 20 Task 3 evaluation cases and record measured metrics."""
    extractor = TranscriptExtractorStage()
    checker = NativeFeatureCheckerStage()
    estimator = ScopeEstimatorStage(hourly_rate=120.0)
    proposal_gen = ProposalGeneratorStage()
    diff_engine = TranscriptDiffStage()
    pipeline = MeetingToScopePipeline()

    results = []

    async def record_case(case_id: str, desc: str, test_fn):
        t0 = time.perf_counter()
        passed = False
        error_msg = None
        details = {}
        try:
            passed, details = await test_fn()
        except Exception as exc:
            error_msg = str(exc)
            passed = False
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        results.append({
            "case_id": case_id,
            "description": desc,
            "status": "PASSED" if passed else "FAILED",
            "score": 1.0 if passed else 0.0,
            "latency_ms": latency_ms,
            "error": error_msg,
            "details": details,
        })

    # Case 01: Functional requirement extraction
    async def c01():
        text = "Client: We need a custom member directory search filter for medical license numbers."
        a = extractor.extract(text)
        ok = len(a.requirements) == 1 and "search filter" in a.requirements[0].title.lower()
        return ok, {"title": a.requirements[0].title}
    await record_case("T3-01", "Functional requirement extraction", c01)

    # Case 02: Integration requirement categorization
    async def c02():
        text = "Client: We require a two-way sync between our directory and Salesforce CRM via API."
        a = extractor.extract(text)
        ok = len(a.requirements) == 1 and a.requirements[0].req_type == RequirementType.INTEGRATION
        return ok, {"type": a.requirements[0].req_type.value}
    await record_case("T3-02", "Integration requirement classification", c02)

    # Case 03: Constraint extraction (Budget & Deadline)
    async def c03():
        text = "Client: We must launch by November 15th, and our maximum budget is limited to $10,000."
        a = extractor.extract(text)
        ok = len(a.constraints) >= 1 and any("10,000" in c or "november" in c.lower() for c in a.constraints)
        return ok, {"constraints": a.constraints}
    await record_case("T3-03", "Project constraint extraction (budget and timeline)", c03)

    # Case 04: Dependency extraction
    async def c04():
        text = "Consultant: What assets will you provide?\nClient: We will provide API credentials and Figma mockups."
        a = extractor.extract(text)
        ok = len(a.dependencies) >= 1
        return ok, {"dependencies": a.dependencies}
    await record_case("T3-04", "External dependency extraction", c04)

    # Case 05: Assumption extraction
    async def c05():
        text = "Client: We assume Brilliant Directories handles the underlying server SSL and database backups."
        a = extractor.extract(text)
        ok = len(a.assumptions) >= 1
        return ok, {"assumptions": a.assumptions}
    await record_case("T3-05", "Implementation assumption extraction", c05)

    # Case 06: Ambiguous requirement detection
    async def c06():
        text = "Client: We need some sort of simple solution for member profiles as soon as possible."
        a = extractor.extract(text)
        vague = [r for r in a.requirements if r.is_ambiguous]
        ok = len(vague) >= 1
        return ok, {"vague_count": len(vague)}
    await record_case("T3-06", "Ambiguous vague phrase detection", c06)

    # Case 07: Targeted follow-up clarification question generation
    async def c07():
        text = "Client: Make sure the directory has some sort of modern look."
        a = extractor.extract(text)
        vague = [r for r in a.requirements if r.is_ambiguous]
        ok = len(vague) >= 1 and vague[0].clarification_question is not None and "criteria" in vague[0].clarification_question.lower()
        return ok, {"question": vague[0].clarification_question}
    await record_case("T3-07", "Follow-up clarification question formulation", c07)

    # Case 08: Native feature check - Stripe payment gateway
    async def c08():
        text = "Client: We need to connect Stripe to accept monthly recurring member subscriptions."
        a = extractor.extract(text)
        v = await checker.verify_requirements(a)
        ok = v.requirements[0].is_native_feature is True and v.requirements[0].estimated_hours <= 4.0
        return ok, {"is_native": v.requirements[0].is_native_feature, "hours": v.requirements[0].estimated_hours}
    await record_case("T3-08", "Native feature recognition: Stripe payments (~3h config)", c08)

    # Case 09: Native feature check - Webhook events
    async def c09():
        text = "Client: We need webhooks to send member registration data to our email system."
        a = extractor.extract(text)
        v = await checker.verify_requirements(a)
        ok = v.requirements[0].is_native_feature is True
        return ok, {"is_native": v.requirements[0].is_native_feature}
    await record_case("T3-09", "Native feature recognition: Webhooks (~3h config)", c09)

    # Case 10: Native feature check - Lead distribution
    async def c10():
        text = "Client: We need automated lead distribution to route inquiries to matching members."
        a = extractor.extract(text)
        v = await checker.verify_requirements(a)
        ok = v.requirements[0].is_native_feature is True
        return ok, {"is_native": v.requirements[0].is_native_feature}
    await record_case("T3-10", "Native feature recognition: Lead distribution (~3h config)", c10)

    # Case 11: Custom development recommendation - Native Mobile App
    async def c11():
        text = "Client: We need a custom native mobile app for iOS and Android in the Apple App Store."
        a = extractor.extract(text)
        v = await checker.verify_requirements(a)
        ok = v.requirements[0].is_native_feature is False and v.requirements[0].estimated_hours >= 16.0
        return ok, {"is_native": v.requirements[0].is_native_feature, "hours": v.requirements[0].estimated_hours}
    await record_case("T3-11", "Custom development requirement: Native Mobile App (~24h)", c11)

    # Case 12: Custom development recommendation - Custom Database Bridge
    async def c12():
        text = "Client: We need a custom bidirectional sync script connecting to internal SQL Server."
        a = extractor.extract(text)
        v = await checker.verify_requirements(a)
        ok = v.requirements[0].is_native_feature is False
        return ok, {"is_native": v.requirements[0].is_native_feature}
    await record_case("T3-12", "Custom development requirement: Database Sync Bridge (~24h)", c12)

    # Case 13: Work Package breakdown & QA allocation
    async def c13():
        text = "Client: We need Stripe payment setup and a custom patient scheduling portal."
        a = extractor.extract(text)
        v = await checker.verify_requirements(a)
        sow = estimator.estimate(v)
        qa_task = next((t for t in sow.tasks if t.work_type == "qa_testing"), None)
        ok = len(sow.tasks) >= 3 and qa_task is not None and qa_task.developer_hours >= 4.0
        return ok, {"tasks_count": len(sow.tasks), "qa_hours": qa_task.developer_hours if qa_task else 0}
    await record_case("T3-13", "Work package breakdown with dedicated QA allocation", c13)

    # Case 14: Accurate budget calculation at $120/hr
    async def c14():
        text = "Client: We need Stripe payments configured."
        a = extractor.extract(text)
        v = await checker.verify_requirements(a)
        sow = estimator.estimate(v)
        expected_cost = sow.total_developer_hours * 120.0
        ok = sow.total_cost_usd == expected_cost
        return ok, {"hours": sow.total_developer_hours, "cost": sow.total_cost_usd}
    await record_case("T3-14", "Budget arithmetic verification at $120/hr", c14)

    # Case 15: Long transcript handling (>100 turns safely processed)
    async def c15():
        long_transcript = "\n".join(
            f"Speaker {i % 2}: Discussion turn number {i} regarding requirement feature number {i}."
            for i in range(120)
        )
        long_transcript += "\nClient: We need Stripe integration and custom CRM sync."
        a = extractor.extract(long_transcript)
        ok = len(a.requirements) >= 2
        return ok, {"requirements_extracted": len(a.requirements)}
    await record_case("T3-15", "Long transcript resilience (>100 conversation turns)", c15)

    # Case 16: Transcript revision diff - Added requirements
    async def c16():
        t1 = "Client: We need Stripe payment setup."
        t2 = "Client: We need Stripe payment setup.\nClient: We also need custom Zapier webhook integration."
        a1 = extractor.extract(t1, version=1)
        s1 = estimator.estimate(a1)
        a2 = extractor.extract(t2, version=2)
        s2 = estimator.estimate(a2)
        diff = diff_engine.compare(a1, s1, a2, s2)
        ok = len(diff.added_requirements) >= 1
        return ok, {"added": diff.added_requirements}
    await record_case("T3-16", "Revision comparison: Added requirements detection", c16)

    # Case 17: Transcript revision diff - Removed requirements
    async def c17():
        t1 = "Client: We need Stripe payment setup.\nClient: We need custom Zapier webhook integration."
        t2 = "Client: We need Stripe payment setup."
        a1 = extractor.extract(t1, version=1)
        s1 = estimator.estimate(a1)
        a2 = extractor.extract(t2, version=2)
        s2 = estimator.estimate(a2)
        diff = diff_engine.compare(a1, s1, a2, s2)
        ok = len(diff.removed_requirements) >= 1
        return ok, {"removed": diff.removed_requirements}
    await record_case("T3-17", "Revision comparison: Removed requirements detection", c17)

    # Case 18: Scope creep detection on significant feature additions
    async def c18():
        t1 = "Client: We need Stripe payment setup."
        t2 = (
            "Client: We need Stripe payment setup.\n"
            "Client: In addition we need a native iOS app.\n"
            "Client: In addition we need a native Android app.\n"
            "Client: In addition we need custom ERP sync."
        )
        a1 = extractor.extract(t1, version=1)
        s1 = estimator.estimate(a1)
        a2 = extractor.extract(t2, version=2)
        s2 = estimator.estimate(a2)
        diff = diff_engine.compare(a1, s1, a2, s2)
        ok = len(diff.scope_creep_warnings) >= 1 and diff.cost_delta > 0
        return ok, {"warnings": diff.scope_creep_warnings, "cost_delta": diff.cost_delta}
    await record_case("T3-18", "Scope creep warning triggered on budget surge & multi-feature add", c18)

    # Case 19: Proposal human approval boundary enforcement
    async def c19():
        text = "Client: We need Stripe payment setup and custom API sync."
        a = extractor.extract(text)
        s = estimator.estimate(a)
        p = proposal_gen.generate(a, s)
        ok = p.is_approved_by_human is False and p.approved_by is None
        return ok, {"is_approved": p.is_approved_by_human}
    await record_case("T3-19", "Client proposal human approval boundary enforced", c19)

    # Case 20: Proposal approval sign-off finalization
    async def c20():
        text = "Client: We need Stripe payment setup."
        a = extractor.extract(text)
        s = estimator.estimate(a)
        p = proposal_gen.generate(a, s)
        approved = proposal_gen.approve_proposal(p, approver_name="Lead Consultant")
        ok = approved.is_approved_by_human is True and approved.approved_by == "Lead Consultant" and approved.approved_at is not None
        return ok, {"approved_by": approved.approved_by, "approved_at": approved.approved_at}
    await record_case("T3-20", "Proposal sign-off finalization with consultant name & timestamp", c20)

    return results


if __name__ == "__main__":
    print("Executing Task 3 Evaluation Suite (20 cases)...")
    res = asyncio.run(run_task3_evaluations())
    passed_count = sum(1 for r in res if r["status"] == "PASSED")
    print(f"\nEvaluation Results: {passed_count}/{len(res)} PASSED")

    out_file = Path("./evals/task3_eval_results.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"Detailed evaluation metrics saved to: {out_file.resolve()}")
