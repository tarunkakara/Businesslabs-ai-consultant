"""Task 3: Meeting-to-Scope Agent - FastAPI Service & UI."""

from pathlib import Path
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from task3_scope_agent.app.config import settings
from task3_scope_agent.app.models import ProposalDocument
from task3_scope_agent.app.pipeline import MeetingToScopePipeline

_pipeline = MeetingToScopePipeline()

app = FastAPI(
    title="BusinessLabs AI Tech Consultant — Meeting-to-Scope Agent",
    description="Multi-stage agent converting meeting transcripts into task breakdowns, SOW, and client proposals.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "scope.html"


class ProcessTranscriptRequest(BaseModel):
    transcript_text: str = Field(..., min_length=20)
    client_name: str = Field(default="Client")
    previous_meeting_id: Optional[str] = None


class ApproveProposalRequest(BaseModel):
    approver_name: str = Field(default="Lead Solutions Architect")


@app.get("/", tags=["UI & General"])
async def root(request: Request):
    """Serves the Meeting-to-Scope Web Dashboard or JSON metadata."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept and TEMPLATE_PATH.exists():
        return HTMLResponse(content=TEMPLATE_PATH.read_text(encoding="utf-8"))
    return JSONResponse({
        "service": "BusinessLabs Meeting-to-Scope Agent",
        "version": "1.0.0",
        "status": "online",
        "ui_url": "/",
        "docs_url": "/docs",
        "default_hourly_rate": settings.default_hourly_rate,
        "require_human_sign_off": settings.require_human_sign_off,
    })


@app.get("/health", tags=["Health"])
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "task3_scope_agent",
        "version": "1.0.0",
        "require_human_sign_off": settings.require_human_sign_off,
    }


@app.post("/api/v1/scope/process", tags=["Pipeline"])
async def process_transcript(request: ProcessTranscriptRequest):
    """Run full 5-stage pipeline on meeting transcript."""
    try:
        proposal, diff = await _pipeline.process_transcript(
            transcript_text=request.transcript_text,
            client_name=request.client_name,
            previous_meeting_id=request.previous_meeting_id,
        )
        return {
            "proposal": proposal,
            "diff": diff,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pipeline processing failed: {str(exc)}")


@app.post("/api/v1/scope/{meeting_id}/approve", response_model=ProposalDocument, tags=["Proposal"])
async def approve_proposal(meeting_id: str, request: ApproveProposalRequest):
    """Human approval sign-off for client proposal."""
    try:
        return _pipeline.approve_proposal(
            meeting_id=meeting_id,
            approver_name=request.approver_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Approval failed: {str(exc)}")


@app.get("/api/v1/scope/{meeting_id}", tags=["Proposal"])
async def get_meeting_scope(meeting_id: str):
    """Retrieve meeting record, SOW, and diff."""
    record = _pipeline.get_meeting(meeting_id)
    if not record:
        raise HTTPException(status_code=404, detail="Meeting record not found")
    return record


@app.get("/api/v1/scope/{meeting_id}/export", tags=["Export"])
async def export_proposal(meeting_id: str):
    """Export formatted client proposal markdown."""
    record = _pipeline.get_meeting(meeting_id)
    if not record:
        raise HTTPException(status_code=404, detail="Meeting record not found")

    p: ProposalDocument = record["proposal"]
    sow = p.scope_of_work

    tasks_md = "\n".join(
        f"| {t.task_id} | {t.title} | {t.work_type} | {t.developer_hours}h | ${t.estimated_cost:,.2f} |"
        for t in sow.tasks
    )

    diff_md = record["diff"].diff_summary_markdown if record.get("diff") else ""

    markdown = (
        f"# {p.title}\n\n"
        f"**Client**: {p.client_name} | **Version**: v{p.version} | **Status**: {'APPROVED' if p.is_approved_by_human else 'DRAFT (Pending Review)'}\n\n"
        f"## Executive Summary\n{p.executive_summary}\n\n"
        f"## Estimated Budget & Timeline\n"
        f"- **Total Hours**: {sow.total_developer_hours} hrs\n"
        f"- **Total Estimated Budget**: ${sow.total_cost_usd:,.2f} USD\n"
        f"- **Estimated Timeline**: {p.estimated_timeline_weeks} weeks\n\n"
        f"## Work Package Breakdown\n"
        f"| ID | Task | Type | Hours | Cost |\n"
        f"|---|---|---|---|---|\n"
        f"{tasks_md}\n\n"
        f"{diff_md}\n"
    )

    return PlainTextResponse(markdown, media_type="text/markdown")
