"""Data Models and Validated Schemas for Task 3: Meeting-to-Scope Agent."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RequirementType(str, Enum):
    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"
    INTEGRATION = "integration"
    SECURITY = "security"


class RequirementItem(BaseModel):
    """Extracted business or technical requirement."""
    id: str
    title: str
    description: str
    req_type: RequirementType = RequirementType.FUNCTIONAL
    is_native_feature: bool = False
    native_feature_evidence: Optional[str] = None
    is_ambiguous: bool = False
    clarification_question: Optional[str] = None
    estimated_hours: float = 0.0


class ExtractedMeetingAnalysis(BaseModel):
    """Structured extraction of raw meeting transcript."""
    meeting_id: str
    version: int = 1
    client_name: str
    project_title: str
    requirements: List[RequirementItem] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TaskEstimate(BaseModel):
    """Work package item breakdown."""
    task_id: str
    title: str
    work_type: str = "custom_development"  # "configuration", "custom_development", "qa_testing"
    developer_hours: float
    hourly_rate: float = 120.0
    estimated_cost: float
    rationale: str


class ScopeOfWork(BaseModel):
    """Aggregated Scope of Work with task breakdown and pricing."""
    sow_id: str
    meeting_id: str
    version: int = 1
    tasks: List[TaskEstimate] = Field(default_factory=list)
    total_developer_hours: float = 0.0
    total_cost_usd: float = 0.0
    native_features_utilized: List[str] = Field(default_factory=list)
    custom_development_packages: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ProposalDocument(BaseModel):
    """Client-facing proposal document requiring human approval before finalization."""
    proposal_id: str
    meeting_id: str
    version: int = 1
    title: str
    client_name: str
    executive_summary: str
    scope_of_work: ScopeOfWork
    estimated_timeline_weeks: float
    unclear_items_notice: Optional[str] = None
    is_approved_by_human: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None


class TranscriptRevisionDiff(BaseModel):
    """Comparison diff between an original and revised transcript."""
    previous_version: int
    new_version: int
    added_requirements: List[str] = Field(default_factory=list)
    removed_requirements: List[str] = Field(default_factory=list)
    hours_delta: float = 0.0
    cost_delta: float = 0.0
    scope_creep_warnings: List[str] = Field(default_factory=list)
    diff_summary_markdown: str
