"""Meeting-to-Scope Coordinated Multi-Stage Pipeline Orchestrator."""

import logging
from typing import Any, Dict, Optional, Tuple
from task3_scope_agent.app.models import (
    ExtractedMeetingAnalysis,
    ProposalDocument,
    ScopeOfWork,
    TranscriptRevisionDiff,
)
from task3_scope_agent.app.stages.diff_stage import TranscriptDiffStage
from task3_scope_agent.app.stages.estimator_stage import ScopeEstimatorStage
from task3_scope_agent.app.stages.extractor_stage import TranscriptExtractorStage
from task3_scope_agent.app.stages.feature_checker import NativeFeatureCheckerStage
from task3_scope_agent.app.stages.proposal_stage import ProposalGeneratorStage

logger = logging.getLogger("scope_agent.pipeline")


class MeetingToScopePipeline:
    """Coordinated pipeline executing all 5 specialized agent stages."""

    def __init__(self):
        self.extractor = TranscriptExtractorStage()
        self.feature_checker = NativeFeatureCheckerStage()
        self.estimator = ScopeEstimatorStage()
        self.proposal_gen = ProposalGeneratorStage()
        self.diff_engine = TranscriptDiffStage()

        # In-memory session store keyed by meeting_id
        self._history: Dict[str, Dict[str, Any]] = {}

    async def process_transcript(
        self,
        transcript_text: str,
        client_name: str = "Acme Corp",
        meeting_id: Optional[str] = None,
        previous_meeting_id: Optional[str] = None,
    ) -> Tuple[ProposalDocument, Optional[TranscriptRevisionDiff]]:
        """Execute stages end-to-end and generate proposal with revision diff if revised."""
        version = 1
        prev_record = None

        if previous_meeting_id and previous_meeting_id in self._history:
            prev_record = self._history[previous_meeting_id]
            version = prev_record["analysis"].version + 1
            m_id = previous_meeting_id
        else:
            m_id = meeting_id

        # Stage 1: Extraction & Ambiguity Detection
        analysis = self.extractor.extract(
            transcript_text=transcript_text,
            client_name=client_name,
            meeting_id=m_id,
            version=version,
        )

        # Stage 2: Knowledge Brain Native Feature Check
        verified_analysis = await self.feature_checker.verify_requirements(analysis)

        # Stage 3: Scope of Work and Hours Estimation
        sow = self.estimator.estimate(verified_analysis)

        # Stage 4: Client Proposal Generation
        proposal = self.proposal_gen.generate(verified_analysis, sow)

        # Stage 5: Revision Diff (if previous version existed)
        diff_record: Optional[TranscriptRevisionDiff] = None
        if prev_record:
            diff_record = self.diff_engine.compare(
                prev_analysis=prev_record["analysis"],
                prev_sow=prev_record["sow"],
                new_analysis=verified_analysis,
                new_sow=sow,
            )

        # Save to history
        self._history[verified_analysis.meeting_id] = {
            "analysis": verified_analysis,
            "sow": sow,
            "proposal": proposal,
            "diff": diff_record,
        }

        return proposal, diff_record

    def approve_proposal(
        self,
        meeting_id: str,
        approver_name: str = "Lead AI Consultant",
    ) -> ProposalDocument:
        """Approve and finalize proposal document."""
        if meeting_id not in self._history:
            raise ValueError(f"Meeting '{meeting_id}' not found in active session store.")

        proposal = self._history[meeting_id]["proposal"]
        approved = self.proposal_gen.approve_proposal(proposal, approver_name)
        self._history[meeting_id]["proposal"] = approved
        return approved

    def get_meeting(self, meeting_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full meeting package (analysis, SOW, proposal, diff)."""
        return self._history.get(meeting_id)
