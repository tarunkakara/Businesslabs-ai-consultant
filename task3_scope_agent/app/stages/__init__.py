"""Pipeline stages for Task 3: Meeting-to-Scope Agent."""

from task3_scope_agent.app.stages.extractor_stage import TranscriptExtractorStage
from task3_scope_agent.app.stages.feature_checker import NativeFeatureCheckerStage
from task3_scope_agent.app.stages.estimator_stage import ScopeEstimatorStage
from task3_scope_agent.app.stages.proposal_stage import ProposalGeneratorStage
from task3_scope_agent.app.stages.diff_stage import TranscriptDiffStage

__all__ = [
    "TranscriptExtractorStage",
    "NativeFeatureCheckerStage",
    "ScopeEstimatorStage",
    "ProposalGeneratorStage",
    "TranscriptDiffStage",
]
