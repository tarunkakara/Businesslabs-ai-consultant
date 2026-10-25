"""SQLite Persistence for Customer Emails, Drafts, and Transparent Audit Logs."""

import email.utils
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def normalize_date_to_iso(date_str: Optional[str]) -> str:
    """Normalize RFC 2822, epoch, or irregular date into standard ISO 8601 UTC string."""
    if not date_str:
        return datetime.now(timezone.utc).isoformat()
    ds = str(date_str).strip()
    # Check if already standard ISO 8601 (starts with YYYY-MM-DD)
    if len(ds) >= 19 and ds[4] == "-" and ds[7] == "-" and ("T" in ds or " " in ds):
        return ds.replace(" ", "T")
    try:
        # Check if numeric epoch (seconds or ms)
        if ds.replace(".", "", 1).isdigit():
            val = float(ds)
            if val > 1e11:  # milliseconds
                val = val / 1000.0
            return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
        # Parse RFC 2822 format (e.g. Mon, 26 Oct 2026 12:00:00 +0000)
        dt = email.utils.parsedate_to_datetime(ds)
        if dt:
            return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    return ds


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
                ("recipient_email", "TEXT"),
            ]:
                if col_name not in existing_cols:
                    try:
                        cursor.execute(f"ALTER TABLE emails ADD COLUMN {col_name} {col_type}")
                    except Exception:
                        pass

            # Mark legacy seed/synthetic emails without user_id as simulated
            cursor.execute("UPDATE emails SET is_simulated = 1 WHERE user_id IS NULL AND is_simulated = 0")

            # One-time migration: normalize any non-ISO dates in existing emails table
            try:
                cursor.execute("SELECT id, received_at FROM emails WHERE received_at IS NOT NULL AND received_at NOT LIKE '20%'")
                legacy_rows = cursor.fetchall()
                for row in legacy_rows:
                    clean_date = normalize_date_to_iso(row["received_at"])
                    cursor.execute("UPDATE emails SET received_at = ? WHERE id = ?", (clean_date, row["id"]))
            except Exception:
                pass

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
        """Insert or replace a batch of customer emails, returning count of processed emails."""
        res = self.save_emails_with_count(emails)
        return res["total"]

    def save_emails_with_count(self, emails: List[CustomerEmail]) -> Dict[str, int]:
        """Insert or replace emails while preserving manual review states and tracking new vs updated counts."""
        new_count = 0
        updated_count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for e in emails:
                # Clean timestamp to standard ISO 8601 for accurate newest-first ordering
                norm_received_at = normalize_date_to_iso(e.received_at)
                e.received_at = norm_received_at

                cursor.execute("SELECT id, status FROM emails WHERE id = ?", (e.id,))
                existing_row = cursor.fetchone()
                if not existing_row:
                    new_count += 1
                else:
                    updated_count += 1

                cursor.execute("""
                    INSERT INTO emails (id, sender_name, sender_email, subject, body, received_at, category, priority, status, user_id, thread_id, is_unread, is_simulated, folder, recipient_email)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        category = COALESCE(emails.category, excluded.category),
                        priority = COALESCE(emails.priority, excluded.priority),
                        status = CASE 
                            WHEN emails.status IN ('approved', 'edited_and_approved', 'sent', 'sent_autonomously') THEN emails.status
                            ELSE excluded.status
                        END,
                        user_id = COALESCE(excluded.user_id, emails.user_id),
                        thread_id = COALESCE(excluded.thread_id, emails.thread_id),
                        is_unread = excluded.is_unread,
                        is_simulated = excluded.is_simulated,
                        folder = COALESCE(excluded.folder, emails.folder),
                        recipient_email = COALESCE(excluded.recipient_email, emails.recipient_email)
                """, (
                    e.id,
                    e.sender_name,
                    e.sender_email,
                    e.subject,
                    e.body,
                    norm_received_at,
                    e.category.value if e.category else None,
                    e.priority.value,
                    e.status.value,
                    e.user_id,
                    e.thread_id,
                    1 if e.is_unread else 0,
                    1 if e.is_simulated else 0,
                    e.folder or "inbox",
                    e.recipient_email,
                ))
            conn.commit()
        return {"total": len(emails), "new": new_count, "updated": updated_count}

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
                recipient_email=d.get("recipient_email"),
            )

    def list_emails(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[str] = None,
        folder: Optional[str] = None,
        source: Optional[str] = None,
        search: Optional[str] = None,
        is_unread: Optional[bool] = None,
        limit: int = 100,
    ) -> List[CustomerEmail]:
        """List emails with multi-tenant user isolation, folder, read/unread, and search filtering."""
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
        else:
            # Unauthenticated requests can only view public simulated demo emails
            if source == "real":
                query += " AND 1 = 0"
            else:
                query += " AND (is_simulated = 1 OR user_id IS NULL)"

        if folder and folder.lower() != "all":
            f_clean = folder.lower()
            if f_clean == "drafts":
                query += " AND (folder = 'drafts' OR status = 'pending_review' OR id IN (SELECT email_id FROM drafts WHERE status NOT IN ('approved', 'sent')))"
                status = None
            elif f_clean == "sent":
                query += " AND (folder = 'sent' OR status IN ('approved', 'edited_and_approved', 'sent_autonomously', 'sent'))"
                status = None
            elif f_clean == "inbox":
                query += " AND (folder = 'inbox' OR folder IS NULL OR folder = '') AND (status IS NULL OR status NOT IN ('approved', 'edited_and_approved', 'sent'))"
            else:
                query += " AND folder = ?"
                params.append(f_clean)

        if category:
            query += " AND category = ?"
            params.append(category)

        if status:
            query += " AND status = ?"
            params.append(status)

        if is_unread is True:
            query += " AND is_unread = 1"
        elif is_unread is False:
            query += " AND is_unread = 0"

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
                        recipient_email=d.get("recipient_email"),
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

    def save_draft_content(
        self,
        email_id: str,
        draft_text: str,
        voice: Optional[CompanyVoice] = None,
        subject: Optional[str] = None,
    ) -> DraftResponse:
        """Save or update draft content without dispatching."""
        existing = self.get_draft_by_email(email_id)
        email = self.get_email(email_id)
        draft_id = existing.draft_id if existing else f"draft_{email_id}"
        subj = subject or (existing.subject if existing else (f"Re: {email.subject}" if email else "Response"))
        v = voice or (existing.voice if existing else CompanyVoice.PROFESSIONAL)
        confidence = existing.confidence_score if existing else 0.95
        sources = existing.kb_sources_cited if existing else []
        now_iso = datetime.now(timezone.utc).isoformat()

        updated_draft = DraftResponse(
            draft_id=draft_id,
            email_id=email_id,
            subject=subj,
            original_draft=draft_text,
            voice=v,
            kb_sources_cited=sources,
            confidence_score=confidence,
            status=DraftStatus.PENDING_REVIEW,
            created_at=now_iso,
        )
        self.save_draft(updated_draft)
        return updated_draft

    def get_folder_counts(
        self,
        user_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> Dict[str, int]:
        """Return accurate counts for inbox, drafts, and sent folders."""
        inbox_emails = self.list_emails(folder="inbox", user_id=user_id, source=source, limit=1000)
        drafts_emails = self.list_emails(folder="drafts", user_id=user_id, source=source, limit=1000)
        sent_emails = self.list_emails(folder="sent", user_id=user_id, source=source, limit=1000)
        return {
            "inbox": len(inbox_emails),
            "drafts": len(drafts_emails),
            "sent": len(sent_emails),
        }
