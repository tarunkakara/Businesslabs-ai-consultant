"""Data models and validated Pydantic schemas for Task 2: Inbox Agent."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EmailCategory(str, Enum):
    """Supported customer email categories."""
    BILLING = "billing"
    FILTERS = "filters"
    DESIGN_SETTINGS = "design_settings"
    PRODUCT_QUESTION = "product_question"
    PROJECT_REQUEST = "project_request"
    REFUND = "refund"
    COMPLAINT = "complaint"
    QUOTE = "quote"
    FOLLOW_UP = "follow_up"


class EmailPriority(str, Enum):
    """Priority level based on urgency and sentiment."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class CompanyVoice(str, Enum):
    """Configurable company voice styles."""
    PROFESSIONAL = "professional"  # Polite, structured, solution-oriented
    EMPATHETIC = "empathetic"      # Understanding, apologetic, highly attentive
    CONCISE = "concise"            # Direct, bullet-pointed, efficient
    EXECUTIVE = "executive"        # Strategic, consultative, authoritative


class DraftStatus(str, Enum):
    """Lifecycle status of an email draft."""
    UNPROCESSED = "unprocessed"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    EDITED_AND_APPROVED = "edited_and_approved"
    SENT_AUTONOMOUSLY = "sent_autonomously"
    REJECTED = "rejected"


class CustomerEmail(BaseModel):
    """Customer email schema."""
    id: str
    sender_email: str
    sender_name: str
    subject: str
    body: str
    received_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    category: Optional[EmailCategory] = None
    priority: EmailPriority = EmailPriority.MEDIUM
    status: DraftStatus = DraftStatus.UNPROCESSED
    user_id: Optional[str] = None
    thread_id: Optional[str] = None
    is_unread: bool = False
    is_simulated: bool = False
    folder: str = "inbox"


class EmailClassification(BaseModel):
    """Structured output for email request classification."""
    category: EmailCategory
    priority: EmailPriority
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    requires_kb_lookup: bool = False
    key_entities: List[str] = Field(default_factory=list)


class DraftResponse(BaseModel):
    """Structured output for generated email reply draft."""
    draft_id: str
    email_id: str
    subject: str
    original_draft: str
    voice: CompanyVoice
    kb_sources_cited: List[Dict[str, Any]] = Field(default_factory=list)
    confidence_score: float = 0.0
    status: DraftStatus = DraftStatus.PENDING_REVIEW
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ReviewSubmission(BaseModel):
    """Human review submission for approval, editing, or rejection."""
    email_id: str
    action: str = Field(..., description="'approve', 'edit_approve', or 'reject'")
    edited_draft: Optional[str] = None
    feedback_notes: Optional[str] = None
    reviewer_name: str = "Support Specialist"


class AuditRecord(BaseModel):
    """Transparent audit record tracking draft similarity, human edits, and autonomy decisions."""
    id: Optional[int] = None
    email_id: str
    category: EmailCategory
    original_draft: str
    human_edited_draft: Optional[str] = None
    final_response: str
    similarity_score: float = Field(..., ge=0.0, le=1.0)
    is_unchanged: bool
    feedback_notes: Optional[str] = None
    reviewer_name: str
    was_autonomous: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CategoryAutonomyStatus(BaseModel):
    """Tracks consecutive unchanged approvals and autonomous sending readiness per category."""
    category: EmailCategory
    consecutive_unchanged_count: int = 0
    autonomous_enabled: bool = False
    revocation_reason: Optional[str] = None
    total_approved: int = 0
    total_edited: int = 0
    quality_score: float = 1.0
