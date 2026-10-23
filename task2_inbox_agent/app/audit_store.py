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
                    status TEXT,
                    user_id TEXT,
                    thread_id TEXT,
                    is_unread INTEGER DEFAULT 0,
                    is_simulated INTEGER DEFAULT 0,
                    folder TEXT DEFAULT 'inbox'
                )
            """)

            # Add columns to existing schema if missing
            existing_cols = [c[1] for c in cursor.execute("PRAGMA table_info(emails)").fetchall()]
            for col_name, col_type in [
                ("user_id", "TEXT"),
                ("thread_id", "TEXT"),
                ("is_unread", "INTEGER DEFAULT 0"),
                ("is_simulated", "INTEGER DEFAULT 0"),
                ("folder", "TEXT DEFAULT 'inbox'"),
            ]:
                if col_name not in existing_cols:
                    try:
                        cursor.execute(f"ALTER TABLE emails ADD COLUMN {col_name} {col_type}")
                    except Exception:
                        pass

            # Mark legacy seed/synthetic emails without user_id as simulated
            cursor.execute("UPDATE emails SET is_simulated = 1 WHERE user_id IS NULL AND is_simulated = 0")

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
                    INSERT INTO emails (id, sender_name, sender_email, subject, body, received_at, category, priority, status, user_id, thread_id, is_unread, is_simulated, folder)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        category = excluded.category,
                        priority = excluded.priority,
                        status = excluded.status,
                        user_id = COALESCE(excluded.user_id, emails.user_id),
                        thread_id = COALESCE(excluded.thread_id, emails.thread_id),
                        is_unread = excluded.is_unread,
                        is_simulated = excluded.is_simulated,
                        folder = excluded.folder
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
                    e.user_id,
                    e.thread_id,
                    1 if e.is_unread else 0,
                    1 if e.is_simulated else 0,
                    e.folder or "inbox",
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
                user_id=d.get("user_id"),
                thread_id=d.get("thread_id"),
                is_unread=bool(d.get("is_unread", 0)),
                is_simulated=bool(d.get("is_simulated", 0)),
                folder=d.get("folder", "inbox"),
            )

    def list_emails(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[str] = None,
        folder: Optional[str] = None,
        source: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
    ) -> List[CustomerEmail]:
        """List emails with multi-tenant user isolation, folder, and search filtering."""
        query = "SELECT * FROM emails WHERE 1=1"
        params: List[Any] = []

        # Multi-tenant mailbox isolation:
        if user_id:
            if source == "real":
                query += " AND user_id = ? AND is_simulated = 0"
                params.append(user_id)
            elif source == "simulated":
                query += " AND (is_simulated = 1 OR user_id IS NULL)"
            else:
                query += " AND (user_id = ? OR is_simulated = 1 OR user_id IS NULL)"
                params.append(user_id)
        elif source == "real":
            query += " AND is_simulated = 0 AND user_id IS NOT NULL"
        elif source == "simulated":
            query += " AND (is_simulated = 1 OR user_id IS NULL)"

        if folder and folder.lower() != "all":
            if folder.lower() == "inbox":
                query += " AND (folder = 'inbox' OR folder IS NULL OR folder = '')"
            else:
                query += " AND folder = ?"
                params.append(folder.lower())

        if category:
            query += " AND category = ?"
            params.append(category)

        if status:
            query += " AND status = ?"
            params.append(status)

        if search:
            query += " AND (subject LIKE ? OR sender_name LIKE ? OR sender_email LIKE ? OR body LIKE ?)"
            s_param = f"%{search}%"
            params.extend([s_param, s_param, s_param, s_param])

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
                        user_id=d.get("user_id"),
                        thread_id=d.get("thread_id"),
                        is_unread=bool(d.get("is_unread", 0)),
                        is_simulated=bool(d.get("is_simulated", 0)),
                        folder=d.get("folder", "inbox"),
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
