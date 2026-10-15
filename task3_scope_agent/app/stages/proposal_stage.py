"""Stage 4: Client-Ready Proposal Generator with Enforced Human Approval Boundary."""

import math
import uuid
from datetime import datetime, timezone
from typing import Optional
from task3_scope_agent.app.config import settings
from task3_scope_agent.app.models import (
    ExtractedMeetingAnalysis,
    ProposalDocument,
    ScopeOfWork,
)


class ProposalGeneratorStage:
    """Generates structured client proposal requiring explicit human approval before release."""

    def generate(
        self,
        analysis: ExtractedMeetingAnalysis,
        sow: ScopeOfWork,
    ) -> ProposalDocument:
        proposal_id = f"PRP-{uuid.uuid4().hex[:6].upper()}"

        timeline_weeks = max(1.0, math.ceil(sow.total_developer_hours / 30.0))

        # Executive summary highlighting native feature cost savings
        native_count = len(sow.native_features_utilized)
        custom_count = len(sow.custom_development_packages)

        exec_summary = (
            f"BusinessLabs AI Tech Consulting is pleased to present this technical scope and implementation proposal "
            f"for {analysis.client_name}. By evaluating your specifications against the Brilliant Directories platform "
            f"architecture, we identified {native_count} core requirements that can be fulfilled using native capabilities, "
            f"substantially reducing custom engineering expenditures. Custom development will focus on {custom_count} "
            f"differentiated feature packages to deliver a high-performance, tailored solution."
        )

        unclear_notice = None
        if analysis.open_questions:
            unclear_notice = (
                f"Note: {len(analysis.open_questions)} items have open clarifying questions. "
                "Final estimates may be adjusted upon receipt of client feedback."
            )

        return ProposalDocument(
            proposal_id=proposal_id,
            meeting_id=analysis.meeting_id,
            version=analysis.version,
            title=f"Technical Implementation Proposal: {analysis.project_title}",
            client_name=analysis.client_name,
            executive_summary=exec_summary,
            scope_of_work=sow,
            estimated_timeline_weeks=float(timeline_weeks),
            unclear_items_notice=unclear_notice,
            is_approved_by_human=False,  # Enforces human boundary by default
            approved_by=None,
            approved_at=None,
        )

    def approve_proposal(
        self,
        proposal: ProposalDocument,
        approver_name: str = "Lead Solutions Architect",
    ) -> ProposalDocument:
        """Approve and finalize client proposal."""
        proposal.is_approved_by_human = True
        proposal.approved_by = approver_name
        proposal.approved_at = datetime.now(timezone.utc).isoformat()
        return proposal
