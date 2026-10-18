"""SQLite Persistence for Customer Emails, Drafts, and Transparent Audit Logs."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from task2_inbox_agent.app.config import settings
from task2_inbox_agent.app.models import (
    AuditRecord,
    CompanyVoice,
    CustomerEmail,
    DraftResponse,
    DraftStatus,
    EmailCategory,
    EmailPriority,
)


class InboxAuditStore:
    """Manages email thread storage and audit trails in SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.sqlite_db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create tables for emails, drafts, and audit history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS emails (
                    id TEXT PRIMARY KEY,
                    sender_name TEXT,
                    sender_email TEXT,
                    subject TEXT,
                    body TEXT,
                    received_at TEXT,
                    category TEXT,
                    priority TEXT,
                    status TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT PRIMARY KEY,
                    email_id TEXT,
                    subject TEXT,
                    original_draft TEXT,
                    voice TEXT,
                    confidence_score REAL,
                    status TEXT,
                    sources_json TEXT,
                    created_at TEXT,
                    FOREIGN KEY (email_id) REFERENCES emails(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id TEXT,
                    category TEXT,
                    original_draft TEXT,
                    human_edited_draft TEXT,
                    final_response TEXT,
                    similarity_score REAL,
                    is_unchanged INTEGER,
                    feedback_notes TEXT,
                    reviewer_name TEXT,
                    was_autonomous INTEGER,
                    timestamp TEXT
                )
            """)
            conn.commit()

    def save_emails(self, emails: List[CustomerEmail]) -> int:
        """Insert or replace a batch of customer emails."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for e in emails:
                cursor.execute("""
                    INSERT INTO emails (id, sender_name, sender_email, subject, body, received_at, category, priority, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        category = excluded.category,
                        priority = excluded.priority,
                        status = excluded.status
                """, (
                    e.id,
                    e.sender_name,
                    e.sender_email,
                    e.subject,
                    e.body,
                    e.received_at,
                    e.category.value if e.category else None,
                    e.priority.value,
                    e.status.value,
                ))
            conn.commit()
        return len(emails)

    def get_email(self, email_id: str) -> Optional[CustomerEmail]:
        """Fetch email record by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM emails WHERE id = ?", (email_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            return CustomerEmail(
                id=d["id"],
                sender_name=d["sender_name"],
                sender_email=d["sender_email"],
                subject=d["subject"],
                body=d["body"],
                received_at=d["received_at"],
                category=EmailCategory(d["category"]) if d.get("category") else None,
                priority=EmailPriority(d["priority"]),
                status=DraftStatus(d["status"]),
            )

    def list_emails(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[CustomerEmail]:
        """List emails with optional category and status filtering."""
        query = "SELECT * FROM emails WHERE 1=1"
        params: List[Any] = []
        if category:
            query += " AND category = ?"
            params.append(category)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY received_at DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            emails = []
            for row in cursor.fetchall():
                d = dict(row)
                emails.append(
                    CustomerEmail(
                        id=d["id"],
                        sender_name=d["sender_name"],
                        sender_email=d["sender_email"],
                        subject=d["subject"],
                        body=d["body"],
                        received_at=d["received_at"],
                        category=EmailCategory(d["category"]) if d.get("category") else None,
                        priority=EmailPriority(d["priority"]),
                        status=DraftStatus(d["status"]),
                    )
                )
            return emails

    def save_draft(self, draft: DraftResponse) -> None:
        """Persist generated draft response."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO drafts (draft_id, email_id, subject, original_draft, voice, confidence_score, status, sources_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    original_draft = excluded.original_draft,
                    status = excluded.status
            """, (
                draft.draft_id,
                draft.email_id,
                draft.subject,
                draft.original_draft,
                draft.voice.value,
                draft.confidence_score,
                draft.status.value,
                json.dumps(draft.kb_sources_cited),
                draft.created_at,
            ))
            # Also update email status
            cursor.execute("UPDATE emails SET status = ? WHERE id = ?", (draft.status.value, draft.email_id))
            conn.commit()

    def get_draft_by_email(self, email_id: str) -> Optional[DraftResponse]:
        """Retrieve existing draft for an email."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM drafts WHERE email_id = ? ORDER BY created_at DESC LIMIT 1", (email_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            return DraftResponse(
                draft_id=d["draft_id"],
                email_id=d["email_id"],
                subject=d["subject"],
                original_draft=d["original_draft"],
                voice=CompanyVoice(d["voice"]),
                confidence_score=d["confidence_score"],
                status=DraftStatus(d["status"]),
                kb_sources_cited=json.loads(d["sources_json"]) if d.get("sources_json") else [],
                created_at=d["created_at"],
            )

    def record_audit(self, audit: AuditRecord) -> int:
        """Store transparent audit record."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO audit_logs (
                    email_id, category, original_draft, human_edited_draft, final_response,
                    similarity_score, is_unchanged, feedback_notes, reviewer_name, was_autonomous, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                audit.email_id,
                audit.category.value,
                audit.original_draft,
                audit.human_edited_draft,
                audit.final_response,
                audit.similarity_score,
                1 if audit.is_unchanged else 0,
                audit.feedback_notes,
                audit.reviewer_name,
                1 if audit.was_autonomous else 0,
                audit.timestamp,
            ))
            # Update email status to approved or edited
            new_status = DraftStatus.APPROVED.value if audit.is_unchanged else DraftStatus.EDITED_AND_APPROVED.value
            if audit.was_autonomous:
                new_status = DraftStatus.SENT_AUTONOMOUSLY.value
            cursor.execute("UPDATE emails SET status = ? WHERE id = ?", (new_status, audit.email_id))
            conn.commit()
            return cursor.lastrowid

    def list_audit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve audit history."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
