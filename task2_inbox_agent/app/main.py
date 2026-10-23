"""Task 2: Inbox Agent - FastAPI Service & Review Dashboard."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from task2_inbox_agent.app.audit_store import InboxAuditStore
from task2_inbox_agent.app.autonomy import AutonomyEngine
from task2_inbox_agent.app.classifier import EmailClassifier
from task2_inbox_agent.app.config import settings
from task2_inbox_agent.app.drafter import ResponseDrafter
from task2_inbox_agent.app.generator import generate_synthetic_emails
from task2_inbox_agent.app.gmail_client import GmailClient
from task2_inbox_agent.app.learning import ContinuousLearningEngine
from task2_inbox_agent.app.models import (
    AuditRecord,
    CompanyVoice,
    CustomerEmail,
    DraftResponse,
    DraftStatus,
    EmailCategory,
    ReviewSubmission,
)
from task2_inbox_agent.app.similarity import calculate_draft_similarity
from pydantic import BaseModel, Field

_store = InboxAuditStore()
_classifier = EmailClassifier()
_learning_engine = ContinuousLearningEngine()
_drafter = ResponseDrafter(learning_engine=_learning_engine)
_autonomy = AutonomyEngine()
_gmail_client = GmailClient()

router = APIRouter(tags=["Task 2: Inbox Agent"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Seed synthetic emails on initial startup if database is empty."""
    existing = _store.list_emails(limit=1)
    if not existing:
        emails = generate_synthetic_emails()
        _store.save_emails(emails)
    yield


app = FastAPI(
    title="BusinessLabs AI Tech Consultant — Inbox Agent",
    description="Customer support automation engine with classification, KB integration, and human-in-the-loop review.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "inbox.html"


@app.get("/", tags=["UI & General"])
async def root(request: Request):
    """Serves the Inbox Review Dashboard or JSON metadata."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept and TEMPLATE_PATH.exists():
        return HTMLResponse(content=TEMPLATE_PATH.read_text(encoding="utf-8"))
    return JSONResponse({
        "service": "BusinessLabs Inbox Agent",
        "version": "1.0.0",
        "status": "online",
        "ui_url": "/",
        "docs_url": "/docs",
        "autonomous_sending_enabled": settings.autonomous_sending_enabled,
    })


@app.get("/health", tags=["Health"])
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "task2_inbox_agent",
        "version": "1.0.0",
        "autonomous_sending_enabled": settings.autonomous_sending_enabled,
    }


@router.post("/api/v1/emails/seed", tags=["Emails"])
async def seed_emails():
    """Seed 55 synthetic customer emails."""
    emails = generate_synthetic_emails()
    count = _store.save_emails(emails)
    return {"status": "success", "count": count}


@router.get("/api/v1/emails", response_model=List[CustomerEmail], tags=["Emails"])
async def list_emails(
    category: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
):
    """List customer emails with optional filters."""
    return _store.list_emails(category=category, status=status, limit=limit)


@router.get("/api/v1/emails/{email_id}", response_model=CustomerEmail, tags=["Emails"])
async def get_email(email_id: str):
    """Get single email record."""
    email = _store.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


@router.post("/api/v1/emails/{email_id}/draft", response_model=DraftResponse, tags=["Drafts"])
async def generate_draft(email_id: str, voice: Optional[CompanyVoice] = None):
    """Classify email, query Knowledge Brain if applicable, and generate structured draft."""
    email = _store.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    classification = _classifier.classify(email)
    # Update email category & priority if unclassified
    email.category = classification.category
    email.priority = classification.priority
    _store.save_emails([email])

    draft = await _drafter.draft_response(
        email=email,
        classification=classification,
        voice=voice or settings.default_voice,
    )
    _store.save_draft(draft)
    return draft


@router.post("/api/v1/emails/{email_id}/review", tags=["Review & Autonomy"])
async def review_draft(email_id: str, submission: ReviewSubmission):
    """Review, edit, approve, or reject draft with transparent similarity and autonomy tracking."""
    email = _store.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    draft = _store.get_draft_by_email(email_id)
    if not draft:
        raise HTTPException(status_code=400, detail="No draft generated yet for this email")

    category = email.category or EmailCategory.PRODUCT_QUESTION

    if submission.action == "reject":
        email.status = DraftStatus.REJECTED
        _store.save_emails([email])
        return {"status": "rejected", "email_id": email_id}

    # Measure similarity between original AI draft and human-submitted text
    final_text = submission.edited_draft or draft.original_draft
    sim_result = calculate_draft_similarity(
        original_draft=draft.original_draft,
        human_edited_draft=final_text,
        unchanged_threshold=settings.quality_similarity_threshold,
    )

    is_unchanged = sim_result["is_unchanged"]

    # Update category autonomy streaks & check revocation rules
    autonomy_status = _autonomy.record_approval(
        category=category,
        is_unchanged=is_unchanged,
    )

    # Check if autonomous dispatch is authorized
    can_auto, auto_msg = _autonomy.can_send_autonomously(category)

    # Log transparent audit record
    audit_rec = AuditRecord(
        email_id=email_id,
        category=category,
        original_draft=draft.original_draft,
        human_edited_draft=final_text if not is_unchanged else None,
        final_response=final_text,
        similarity_score=sim_result["similarity_score"],
        is_unchanged=is_unchanged,
        feedback_notes=submission.feedback_notes,
        reviewer_name=submission.reviewer_name,
        was_autonomous=can_auto,
    )
    _store.record_audit(audit_rec)

    # Trigger continuous 5-minute learning loop if human modified the draft
    learned_rule = None
    if not is_unchanged:
        learned_rule = _learning_engine.learn_from_edit(
            category=category.value,
            original_draft=draft.original_draft,
            user_edited_draft=final_text,
            reviewer_name=submission.reviewer_name,
        )

    # Update email status
    email.status = DraftStatus.EDITED_AND_APPROVED if not is_unchanged else DraftStatus.APPROVED
    _store.save_emails([email])

    return {
        "status": "approved",
        "is_unchanged": is_unchanged,
        "similarity_score": sim_result["similarity_score"],
        "diff_summary": sim_result["diff_summary"],
        "consecutive_streak": autonomy_status.consecutive_unchanged_count,
        "autonomous_authorized": can_auto,
        "autonomy_message": auto_msg,
        "learned_rule": learned_rule,
    }


@router.get("/api/v1/autonomy/status", tags=["Review & Autonomy"])
async def get_autonomy_status():
    """Get streak counts, quality scores, and autonomous sending readiness across all categories."""
    return _autonomy.get_all_statuses()


@router.get("/api/v1/audit/logs", tags=["Audit"])
async def list_audit_logs(limit: int = Query(default=50, ge=1, le=100)):
    """Retrieve transparent audit records."""
    return _store.list_audit_logs(limit=limit)


@router.get("/api/v1/emails/{email_id}/export", tags=["Export"])
async def export_email(email_id: str):
    """Export approved email response as formatted text."""
    email = _store.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    draft = _store.get_draft_by_email(email_id)
    if not draft:
        raise HTTPException(status_code=400, detail="No draft found")

    export_content = (
        f"From: BusinessLabs Support <support@businesslabs.ai>\n"
        f"To: {email.sender_name} <{email.sender_email}>\n"
        f"Subject: Re: {email.subject}\n"
        f"In-Reply-To: <{email.id}@customers.businesslabs.ai>\n"
        f"Status: {draft.status.value}\n"
        f"============================================================\n\n"
        f"{draft.original_draft}\n"
    )
    return PlainTextResponse(export_content, media_type="text/plain")


# Real Gmail Integration & Evaluator Simulation Endpoints
class GmailTestRequest(BaseModel):
    email: str
    app_password: str


class GmailSyncRequest(BaseModel):
    email: Optional[str] = None
    app_password: Optional[str] = None
    limit: int = 10


class GmailSendRequest(BaseModel):
    to_email: str
    subject: str
    body_text: str
    from_email: Optional[str] = None
    app_password: Optional[str] = None
    email_id: Optional[str] = None


class SimulateEmailRequest(BaseModel):
    subject: str
    body: str
    sender_name: str = "Marcus Vance"
    sender_email: str = "marcus@enterprise-directory.com"
    category: Optional[str] = None


class AutonomyConfigRequest(BaseModel):
    streak_threshold: int = Field(default=20, ge=1, le=100)


@router.post("/api/v1/gmail/test", tags=["Gmail Integration"])
async def test_gmail_connection(req: GmailTestRequest):
    """Test connection to user's real Gmail account via IMAP."""
    result = _gmail_client.test_connection(user_email=req.email, app_password=req.app_password)
    return result


@router.post("/api/v1/gmail/sync", tags=["Gmail Integration"])
async def sync_gmail_inbox(req: GmailSyncRequest):
    """Fetch new incoming emails from user's real Gmail INBOX and ingest into inbox database."""
    fetched = _gmail_client.fetch_inbox_emails(
        limit=req.limit,
        user_email=req.email,
        app_password=req.app_password,
    )
    if not fetched:
        return {"status": "success", "count": 0, "message": "No new emails found or credentials not provided."}

    # Automatically classify ingested emails
    for e in fetched:
        cl = _classifier.classify(e)
        e.category = cl.category
        e.priority = cl.priority

    count = _store.save_emails(fetched)
    return {"status": "success", "count": count, "message": f"Successfully synced {count} emails from Gmail."}


@router.post("/api/v1/gmail/send", tags=["Gmail Integration"])
async def send_email_via_gmail(req: GmailSendRequest):
    """Send approved email response or custom message via user's real Gmail SMTP account."""
    result = _gmail_client.send_email(
        to_email=req.to_email,
        subject=req.subject,
        body_text=req.body_text,
        from_email=req.from_email,
        app_password=req.app_password,
    )
    if result.get("success") and req.email_id:
        email = _store.get_email(req.email_id)
        if email:
            email.status = DraftStatus.APPROVED
            _store.save_emails([email])
    return result


@router.post("/api/v1/emails/simulate", tags=["Simulation & Testing"])
async def simulate_incoming_email(req: SimulateEmailRequest):
    """Simulate an incoming customer email for evaluators to test classification & drafting without credentials."""
    category_enum = None
    if req.category:
        try:
            category_enum = EmailCategory(req.category)
        except ValueError:
            pass

    email = _gmail_client.generate_simulated_email(
        subject=req.subject,
        body=req.body,
        sender_name=req.sender_name,
        sender_email=req.sender_email,
        category=category_enum,
    )
    cl = _classifier.classify(email)
    email.category = cl.category
    email.priority = cl.priority

    _store.save_emails([email])
    return {"status": "success", "email": email, "classification": cl}


@router.get("/api/v1/learning/rules", tags=["Continuous Learning"])
async def get_learned_rules(category: Optional[str] = Query(default=None)):
    """Retrieve style rules learned from human reviewer edits in the 5-minute loop."""
    return _learning_engine.get_learned_rules(category=category)


@router.post("/api/v1/autonomy/config", tags=["Review & Autonomy"])
async def configure_autonomy(req: AutonomyConfigRequest):
    """Set configurable consecutive approval streak required before enabling autonomous sending."""
    _autonomy.set_threshold(req.streak_threshold)
    return {
        "status": "success",
        "streak_threshold": _autonomy.streak_threshold,
        "message": f"Streak threshold updated to {req.streak_threshold} consecutive unchanged approvals.",
    }


app.include_router(router)
