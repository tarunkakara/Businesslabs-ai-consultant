"""Task 2: Inbox Agent - FastAPI Service & Review Dashboard."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from task2_inbox_agent.app.audit_store import InboxAuditStore
from task2_inbox_agent.app.autonomy import AutonomyEngine
from task2_inbox_agent.app.classifier import EmailClassifier
from task2_inbox_agent.app.config import settings
from task2_inbox_agent.app.drafter import ResponseDrafter
from task2_inbox_agent.app.generator import generate_synthetic_emails
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

_store = InboxAuditStore()
_classifier = EmailClassifier()
_drafter = ResponseDrafter()
_autonomy = AutonomyEngine()


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


@app.post("/api/v1/emails/seed", tags=["Emails"])
async def seed_emails():
    """Seed 55 synthetic customer emails."""
    emails = generate_synthetic_emails()
    count = _store.save_emails(emails)
    return {"status": "success", "count": count}


@app.get("/api/v1/emails", response_model=List[CustomerEmail], tags=["Emails"])
async def list_emails(
    category: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
):
    """List customer emails with optional filters."""
    return _store.list_emails(category=category, status=status, limit=limit)


@app.get("/api/v1/emails/{email_id}", response_model=CustomerEmail, tags=["Emails"])
async def get_email(email_id: str):
    """Get single email record."""
    email = _store.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


@app.post("/api/v1/emails/{email_id}/draft", response_model=DraftResponse, tags=["Drafts"])
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


@app.post("/api/v1/emails/{email_id}/review", tags=["Review & Autonomy"])
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

    return {
        "status": "approved",
        "is_unchanged": is_unchanged,
        "similarity_score": sim_result["similarity_score"],
        "diff_summary": sim_result["diff_summary"],
        "consecutive_streak": autonomy_status.consecutive_unchanged_count,
        "autonomous_authorized": can_auto,
        "autonomy_message": auto_msg,
    }


@app.get("/api/v1/autonomy/status", tags=["Review & Autonomy"])
async def get_autonomy_status():
    """Get streak counts, quality scores, and autonomous sending readiness across all categories."""
    return _autonomy.get_all_statuses()


@app.get("/api/v1/audit/logs", tags=["Audit"])
async def list_audit_logs(limit: int = Query(default=50, ge=1, le=100)):
    """Retrieve transparent audit records."""
    return _store.list_audit_logs(limit=limit)


@app.get("/api/v1/emails/{email_id}/export", tags=["Export"])
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
