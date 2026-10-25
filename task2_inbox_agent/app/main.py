"""Task 2: Inbox Agent - FastAPI Service & Review Dashboard."""

import uuid
from datetime import datetime, timezone
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
from task2_inbox_agent.app.gmail_client import GmailClient, GmailOAuthClient
from task2_inbox_agent.app.learning import ContinuousLearningEngine
from auth import AuthStore

_auth_store = AuthStore()


def get_session_user(request: Request) -> Optional[Dict[str, Any]]:
    """Extract authenticated session user from cookie or authorization header."""
    token = request.cookies.get("session_token")
    if not token:
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            token = auth_hdr.split(" ", 1)[1].strip()
    if token:
        return _auth_store.validate_session(token)
    return None
from task2_inbox_agent.app.models import (
    AuditRecord,
    CompanyVoice,
    CustomerEmail,
    DraftResponse,
    DraftStatus,
    EmailCategory,
    ReviewSubmission,
    normalize_voice,
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
    request: Request,
    category: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    folder: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default="all"),
    search: Optional[str] = Query(default=None),
    is_unread: Optional[bool] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
):
    """List customer emails with multi-tenant user isolation, folder selection, and search."""
    user = get_session_user(request)
    user_id = user["id"] if user else None
    return _store.list_emails(
        category=category,
        status=status,
        user_id=user_id,
        folder=folder,
        source=source,
        search=search,
        is_unread=is_unread,
        limit=limit,
    )


@router.get("/api/v1/emails/{email_id}", response_model=CustomerEmail, tags=["Emails"])
async def get_email(email_id: str):
    """Get single email record."""
    email = _store.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


class SaveDraftBody(BaseModel):
    draft_text: str
    voice: Optional[Any] = None
    subject: Optional[str] = None


@router.post("/api/v1/emails/{email_id}/draft", response_model=DraftResponse, tags=["Drafts"])
async def generate_draft(email_id: str, voice: Optional[Any] = None, force_regenerate: bool = False):
    """Classify email, query Knowledge Brain if applicable, and generate structured draft."""
    email = _store.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    selected_voice = normalize_voice(voice or settings.default_voice)

    if not force_regenerate:
        existing = _store.get_draft_by_email(email_id)
        if existing:
            # If user explicitly requested a specific voice and it differs from existing draft, regenerate!
            if voice is None or existing.voice == selected_voice:
                return existing

    classification = _classifier.classify(email)
    # Update email category & priority if unclassified
    email.category = classification.category
    email.priority = classification.priority
    _store.save_emails([email])

    draft = await _drafter.draft_response(
        email=email,
        classification=classification,
        voice=selected_voice,
    )
    _store.save_draft(draft)
    return draft


@router.get("/api/v1/emails/{email_id}/draft", response_model=DraftResponse, tags=["Drafts"])
async def get_draft(email_id: str):
    """Retrieve existing draft for an email."""
    draft = _store.get_draft_by_email(email_id)
    if not draft:
        raise HTTPException(status_code=404, detail="No draft found for this email")
    return draft


@router.post("/api/v1/emails/{email_id}/save-draft", response_model=DraftResponse, tags=["Drafts"])
async def save_draft(request: Request, email_id: str, body: SaveDraftBody):
    """Save or update draft content without dispatching, and synchronize to Gmail Drafts if connected."""
    user = get_session_user(request)
    user_id = user["id"] if user else None

    email = _store.get_email(email_id)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    v = normalize_voice(body.voice)
    saved = _store.save_draft_content(
        email_id=email_id,
        draft_text=body.draft_text,
        voice=v,
        subject=body.subject,
    )

    # Sync to real Gmail Drafts if user has connected Gmail OAuth
    if user_id and not email.is_simulated:
        try:
            gmail_res = await GmailOAuthClient.create_or_update_user_draft(
                user_id=user_id,
                auth_store=_auth_store,
                to_email=email.sender_email,
                subject=saved.subject,
                body_text=saved.original_draft,
                thread_id=email.thread_id,
                existing_draft_id=email.gmail_draft_id,
            )
            if gmail_res.get("success"):
                d_id = gmail_res.get("gmail_draft_id")
                email.gmail_draft_id = d_id
                saved.gmail_draft_id = d_id
                _store.save_emails([email])
                _store.save_draft(saved)
        except Exception as exc:
            logger.warning("Could not sync draft to Gmail: %s", exc)

    return saved


@router.get("/api/v1/emails/counts", tags=["Emails"])
async def get_email_counts(request: Request, source: Optional[str] = None):
    """Return count of emails in inbox, drafts, and sent folders."""
    user = get_session_user(request)
    user_id = user["id"] if user else None
    return _store.get_folder_counts(user_id=user_id, source=source)


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
    folder: Optional[str] = "inbox"
    query: Optional[str] = None
    limit: int = 20
    force_full_sync: bool = False
    page_token: Optional[str] = None


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


@router.get("/api/v1/gmail/sync/status", tags=["Gmail Integration"])
async def get_gmail_sync_status(request: Request):
    """Retrieve current synchronization checkpoint, timestamp, and status for authenticated user."""
    user = get_session_user(request)
    if not user:
        return {"connected": False, "status": "unauthenticated"}
    return _auth_store.get_gmail_sync_state(user["id"])


@router.post("/api/v1/gmail/sync", tags=["Gmail Integration"])
async def sync_gmail_inbox(request: Request, req: Optional[GmailSyncRequest] = None):
    """Fetch new incoming emails from user's authorized Gmail account via OAuth 2.0 REST API."""
    user = get_session_user(request)
    user_id = user["id"] if user else None

    limit = req.limit if req else 20
    folder = req.folder if req and req.folder else "inbox"
    query = req.query if req and req.query else None
    force_full = req.force_full_sync if req else False
    page_token = req.page_token if req else None

    # 1. If user is authenticated, attempt real Gmail OAuth REST API fetch
    if user_id:
        sync_result = await GmailOAuthClient.sync_user_emails(
            user_id=user_id,
            auth_store=_auth_store,
            folder=folder,
            search_query=query,
            limit=limit,
            page_token=page_token,
            force_full_sync=force_full,
        )

        if sync_result["status"] == "success":
            fetched = sync_result.get("emails", [])
            save_stats = {"total": 0, "new": 0, "updated": 0}
            if fetched:
                for e in fetched:
                    cl = _classifier.classify(e)
                    e.category = cl.category
                    e.priority = cl.priority
                save_stats = _store.save_emails_with_count(fetched)

            conn_info = _auth_store.get_gmail_token(user_id)
            gmail_addr = conn_info.get("gmail_address") if conn_info else "Gmail"

            return {
                "status": "success",
                "count": save_stats["total"],
                "new_count": save_stats["new"],
                "updated_count": save_stats["updated"],
                "connected_email": gmail_addr,
                "history_id": sync_result.get("history_id"),
                "next_page_token": sync_result.get("next_page_token"),
                "is_incremental": sync_result.get("is_incremental", False),
                "last_sync_time": datetime.now(timezone.utc).isoformat(),
                "message": sync_result.get("message") or f"Successfully synced {save_stats['total']} emails from {gmail_addr}.",
            }
        elif sync_result["status"] == "in_progress":
            return {
                "status": "in_progress",
                "count": 0,
                "new_count": 0,
                "message": "Synchronization is already in progress. Please wait a moment.",
            }
        elif sync_result["status"] == "error":
            return {
                "status": "error",
                "count": 0,
                "new_count": 0,
                "message": sync_result.get("message", "Gmail synchronization failed."),
            }

    # 2. Check if legacy IMAP credentials were provided in request body
    if req and req.email and req.app_password:
        legacy_fetched = _gmail_client.fetch_inbox_emails(
            limit=limit,
            user_email=req.email,
            app_password=req.app_password,
        )
        if legacy_fetched:
            for e in legacy_fetched:
                if user_id:
                    e.user_id = user_id
                cl = _classifier.classify(e)
                e.category = cl.category
                e.priority = cl.priority
            save_stats = _store.save_emails_with_count(legacy_fetched)
            return {
                "status": "success",
                "count": save_stats["total"],
                "new_count": save_stats["new"],
                "updated_count": save_stats["updated"],
                "last_sync_time": datetime.now(timezone.utc).isoformat(),
                "message": f"Successfully synced {save_stats['total']} emails via IMAP.",
            }

    # 3. If not connected, return clear guidance
    return {
        "status": "not_connected",
        "count": 0,
        "new_count": 0,
        "message": "Gmail account not connected. Please click 'Connect Gmail' to authorize your real inbox.",
    }


@router.post("/api/v1/gmail/send", tags=["Gmail Integration"])
async def send_email_via_gmail(request: Request, req: GmailSendRequest):
    """Send approved email response from user's connected Gmail account via OAuth REST API."""
    user = get_session_user(request)
    user_id = user["id"] if user else None

    email_rec = _store.get_email(req.email_id) if req.email_id else None
    thread_id = email_rec.thread_id if email_rec else None
    is_sim = email_rec.is_simulated if email_rec else False

    # Simulated email response handling
    if is_sim:
        if email_rec:
            email_rec.status = DraftStatus.APPROVED
            _store.save_emails([email_rec])

        # Record sent reply in emails table for the Sent folder
        sent_email = CustomerEmail(
            id=f"sent_{uuid.uuid4().hex[:12]}",
            sender_name=(user.get("name") or user.get("email") or "Support Consultant") if user else "Support Consultant",
            sender_email=req.from_email or (user.get("email") if user else "support@businesslabs.ai"),
            recipient_email=req.to_email,
            subject=req.subject if req.subject.startswith("Re:") else f"Re: {req.subject}",
            body=req.body_text,
            received_at=datetime.now(timezone.utc).isoformat(),
            category=email_rec.category if email_rec else None,
            priority=email_rec.priority if email_rec else EmailPriority.MEDIUM,
            status=DraftStatus.APPROVED,
            user_id=user_id,
            thread_id=thread_id,
            is_unread=False,
            is_simulated=True,
            folder="sent",
        )
        _store.save_emails([sent_email])

        return {
            "success": True,
            "simulated": True,
            "message": f"[Simulator] Reply approved and simulated dispatch to {req.to_email} recorded.",
        }

    # Real Gmail OAuth dispatch
    if user_id:
        res = await GmailOAuthClient.send_user_reply(
            user_id=user_id,
            auth_store=_auth_store,
            to_email=req.to_email,
            subject=req.subject,
            body_text=req.body_text,
            thread_id=thread_id,
        )
        if res.get("success"):
            if email_rec:
                email_rec.status = DraftStatus.APPROVED
                _store.save_emails([email_rec])

            # Record sent reply in emails table for the Sent folder
            sent_email = CustomerEmail(
                id=f"sent_{uuid.uuid4().hex[:12]}",
                sender_name=(user.get("name") or user.get("email") or "Me") if user else "Me",
                sender_email=res.get("from_address") or (user.get("email") if user else "me@gmail.com"),
                recipient_email=req.to_email,
                subject=req.subject if req.subject.startswith("Re:") else f"Re: {req.subject}",
                body=req.body_text,
                received_at=datetime.now(timezone.utc).isoformat(),
                category=email_rec.category if email_rec else None,
                priority=email_rec.priority if email_rec else EmailPriority.MEDIUM,
                status=DraftStatus.APPROVED,
                user_id=user_id,
                thread_id=thread_id,
                is_unread=False,
                is_simulated=False,
                folder="sent",
            )
            _store.save_emails([sent_email])
            return res
        elif not req.app_password:
            return res

    # Legacy SMTP fallback
    if req.app_password:
        smtp_res = _gmail_client.send_email(
            to_email=req.to_email,
            subject=req.subject,
            body_text=req.body_text,
            from_email=req.from_email,
            app_password=req.app_password,
        )
        if smtp_res.get("success") and email_rec:
            email_rec.status = DraftStatus.APPROVED
            _store.save_emails([email_rec])

            sent_email = CustomerEmail(
                id=f"sent_{uuid.uuid4().hex[:12]}",
                sender_name=(user.get("name") or user.get("email") or "Me") if user else "Me",
                sender_email=req.from_email or (user.get("email") if user else "me@gmail.com"),
                recipient_email=req.to_email,
                subject=req.subject if req.subject.startswith("Re:") else f"Re: {req.subject}",
                body=req.body_text,
                received_at=datetime.now(timezone.utc).isoformat(),
                category=email_rec.category if email_rec else None,
                priority=email_rec.priority if email_rec else EmailPriority.MEDIUM,
                status=DraftStatus.APPROVED,
                user_id=user_id,
                thread_id=thread_id,
                is_unread=False,
                is_simulated=False,
                folder="sent",
            )
            _store.save_emails([sent_email])
        return smtp_res

    return {
        "success": False,
        "message": "Cannot send email: user not authenticated or Gmail account not connected via OAuth.",
    }


@router.post("/api/v1/emails/simulate", tags=["Simulation & Testing"])
async def simulate_incoming_email(request: Request, req: SimulateEmailRequest):
    """Simulate an incoming customer email for evaluators to test classification & drafting without credentials."""
    user = get_session_user(request)
    user_id = user["id"] if user else None

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
        user_id=user_id,
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
