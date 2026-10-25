"""Real Gmail Integration Client with OAuth 2.0 REST API & Threaded Replies.

Supports:
- Per-user OAuth 2.0 authorization with automatic token refresh
- Secure encrypted token retrieval and revocation
- Real incoming email sync across Inbox, Sent, Drafts, and search queries
- Threaded reply dispatch via Gmail REST API (users.me.messages.send)
- Distinguishes real Gmail emails from simulated test emails
- Backward-compatible IMAP/SMTP fallback
"""

import asyncio
import base64
import email
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import imaplib
import logging
import os
import smtplib
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid

import httpx

from task2_inbox_agent.app.audit_store import normalize_date_to_iso
from task2_inbox_agent.app.models import (
    CustomerEmail,
    DraftStatus,
    EmailCategory,
    EmailPriority,
)

logger = logging.getLogger("inbox_agent.gmail")

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_GMAIL_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_GMAIL_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET", "")


class GmailOAuthClient:
    """Manages real per-user Gmail operations via Google's REST API with incremental sync and locking."""

    _sync_locks: Dict[str, asyncio.Lock] = {}

    @classmethod
    def _get_user_lock(cls, user_id: str) -> asyncio.Lock:
        """Get or create asyncio lock for this user to avoid overlapping/concurrent sync requests."""
        if user_id not in cls._sync_locks:
            cls._sync_locks[user_id] = asyncio.Lock()
        return cls._sync_locks[user_id]

    @staticmethod
    def _decode_mime_header(val: Optional[str]) -> str:
        """Decode MIME encoded-word RFC 2047 headers (e.g. =?UTF-8?B?...?=)."""
        if not val:
            return ""
        try:
            parts = decode_header(val)
            decoded_chunks = []
            for chunk, enc in parts:
                if isinstance(chunk, bytes):
                    decoded_chunks.append(chunk.decode(enc or "utf-8", errors="replace"))
                else:
                    decoded_chunks.append(str(chunk))
            return "".join(decoded_chunks).strip()
        except Exception:
            return str(val).strip()

    @staticmethod
    async def get_valid_token(user_id: str, auth_store: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Retrieve valid access token, auto-refreshing via refresh_token if expired.

        Returns (access_token, gmail_address, error_message).
        """
        token_info = auth_store.get_gmail_token(user_id)
        if not token_info:
            return None, None, "No Gmail account connected. Please click 'Connect Gmail' to authorize."

        access_token = token_info.get("access_token")
        refresh_token = token_info.get("refresh_token")
        gmail_address = token_info.get("gmail_address")

        # If token is still valid (with 60-second buffer)
        if not token_info.get("is_expired") and access_token:
            return access_token, gmail_address, None

        # Expired: attempt refresh
        if not refresh_token:
            return None, gmail_address, "Gmail authorization expired. Please reconnect your Gmail account."

        client_id = os.getenv("GOOGLE_GMAIL_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or GOOGLE_CLIENT_ID
        client_secret = os.getenv("GOOGLE_GMAIL_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or GOOGLE_CLIENT_SECRET
        if not client_id or not client_secret:
            return None, gmail_address, "Google OAuth client credentials not configured on server."

        try:
            async with httpx.AsyncClient() as http_client:
                resp = await http_client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    headers={"Accept": "application/json"},
                    timeout=15.0,
                )
                if resp.status_code != 200:
                    logger.warning("Token refresh failed (status %d): %s", resp.status_code, resp.text)
                    return None, gmail_address, "Gmail authorization revoked or expired. Please reconnect."

                data = resp.json()
                new_access = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                auth_store.update_gmail_access_token(user_id, new_access, expires_in)
                return new_access, gmail_address, None
        except Exception as exc:
            logger.error("Error refreshing Gmail token: %s", exc)
            return None, gmail_address, f"Token refresh error: {str(exc)}"

    @classmethod
    def _parse_gmail_message(cls, msg_data: Dict[str, Any], user_id: str, folder_lower: str) -> CustomerEmail:
        """Parse raw Gmail REST API message object into CustomerEmail domain model."""
        msg_id = msg_data.get("id", "")
        thread_id = msg_data.get("threadId")
        label_ids = msg_data.get("labelIds", [])
        is_unread = "UNREAD" in label_ids

        payload = msg_data.get("payload", {})
        p_headers = payload.get("headers", [])
        header_map = {h["name"].lower(): h["value"] for h in p_headers if "name" in h and "value" in h}

        raw_subject = header_map.get("subject", "No Subject")
        subject = cls._decode_mime_header(raw_subject) or "No Subject"

        raw_from = header_map.get("from", "Unknown Sender")
        from_decoded = cls._decode_mime_header(raw_from) or "Unknown Sender"
        sender_name = from_decoded
        sender_email = from_decoded
        if "<" in from_decoded and ">" in from_decoded:
            sender_name = from_decoded.split("<")[0].strip().strip('"').strip("'")
            sender_email = from_decoded.split("<")[1].split(">")[0].strip()

        # Date normalization: Use internalDate (ms epoch) if available, else parse header Date
        internal_ms = msg_data.get("internalDate")
        if internal_ms:
            try:
                epoch_sec = int(internal_ms) / 1000.0
                date_val = datetime.fromtimestamp(epoch_sec, tz=timezone.utc).isoformat()
            except Exception:
                date_val = normalize_date_to_iso(header_map.get("date"))
        else:
            date_val = normalize_date_to_iso(header_map.get("date"))

        body_text = cls._extract_body(payload)

        return CustomerEmail(
            id=f"GMAIL-{msg_id}",
            user_id=user_id,
            thread_id=thread_id,
            sender_name=sender_name or "Client",
            sender_email=sender_email or "client@example.com",
            subject=subject,
            body=body_text.strip() or "(Empty email body)",
            received_at=date_val,
            priority=EmailPriority.MEDIUM,
            status=DraftStatus.UNPROCESSED,
            is_unread=is_unread,
            is_simulated=False,
            folder=folder_lower,
        )

    @classmethod
    async def sync_user_emails(
        cls,
        user_id: str,
        auth_store: Any,
        folder: str = "inbox",
        search_query: Optional[str] = None,
        limit: int = 20,
        page_token: Optional[str] = None,
        force_full_sync: bool = False,
    ) -> Dict[str, Any]:
        """Perform robust Gmail synchronization with incremental historyId support and concurrency locking."""
        lock = cls._get_user_lock(user_id)
        if lock.locked():
            return {
                "status": "in_progress",
                "emails": [],
                "count": 0,
                "new_count": 0,
                "message": "Synchronization is already active for this account.",
            }

        async with lock:
            access_token, gmail_address, err = await cls.get_valid_token(user_id, auth_store)
            if err or not access_token:
                if hasattr(auth_store, "update_gmail_sync_state"):
                    auth_store.update_gmail_sync_state(user_id, status="error", message=err or "Not authenticated")
                return {
                    "status": "not_connected" if "No Gmail account connected" in (err or "") else "error",
                    "emails": [],
                    "count": 0,
                    "new_count": 0,
                    "message": err or "Failed to obtain valid Gmail credentials.",
                }

            folder_lower = (folder or "inbox").lower()
            headers = {"Authorization": f"Bearer {access_token}"}

            try:
                async with httpx.AsyncClient() as http_client:
                    # 1. Check for incremental sync via historyId if inbox folder
                    sync_state = auth_store.get_gmail_sync_state(user_id) if hasattr(auth_store, "get_gmail_sync_state") else {}
                    last_history_id = sync_state.get("last_history_id") if isinstance(sync_state, dict) else None

                    if last_history_id and folder_lower == "inbox" and not search_query and not page_token and not force_full_sync:
                        try:
                            hist_resp = await http_client.get(
                                f"{GMAIL_API_BASE}/history",
                                params={"startHistoryId": str(last_history_id), "historyTypes": "messageAdded"},
                                headers=headers,
                                timeout=20.0,
                            )
                            if hist_resp.status_code == 200:
                                hist_data = hist_resp.json()
                                new_history_id = hist_data.get("historyId", last_history_id)
                                new_msg_ids: List[str] = []
                                for rec in hist_data.get("history", []):
                                    for msg_added in rec.get("messagesAdded", []):
                                        m = msg_added.get("message", {})
                                        mid = m.get("id")
                                        if mid and mid not in new_msg_ids:
                                            new_msg_ids.append(mid)

                                if new_msg_ids:
                                    new_emails: List[CustomerEmail] = []
                                    for mid in new_msg_ids[:limit]:
                                        msg_resp = await http_client.get(
                                            f"{GMAIL_API_BASE}/messages/{mid}",
                                            params={"format": "full"},
                                            headers=headers,
                                            timeout=15.0,
                                        )
                                        if msg_resp.status_code == 200:
                                            email_obj = cls._parse_gmail_message(msg_resp.json(), user_id, folder_lower)
                                            new_emails.append(email_obj)

                                    if hasattr(auth_store, "update_gmail_sync_state"):
                                        auth_store.update_gmail_sync_state(
                                            user_id,
                                            last_history_id=new_history_id,
                                            status="success",
                                            message=f"Received {len(new_emails)} new email(s)",
                                        )
                                    return {
                                        "status": "success",
                                        "emails": new_emails,
                                        "count": len(new_emails),
                                        "new_count": len(new_emails),
                                        "gmail_address": gmail_address,
                                        "history_id": new_history_id,
                                        "is_incremental": True,
                                        "message": f"Successfully synced {len(new_emails)} new email(s) from {gmail_address}.",
                                    }
                                else:
                                    # No new message additions in history
                                    if hasattr(auth_store, "update_gmail_sync_state"):
                                        auth_store.update_gmail_sync_state(
                                            user_id,
                                            last_history_id=new_history_id,
                                            status="success",
                                            message="Inbox is up to date",
                                        )
                                    return {
                                        "status": "success",
                                        "emails": [],
                                        "count": 0,
                                        "new_count": 0,
                                        "gmail_address": gmail_address,
                                        "history_id": new_history_id,
                                        "is_incremental": True,
                                        "message": f"Inbox is up to date. No new emails found in {gmail_address}.",
                                    }
                            elif hist_resp.status_code == 404:
                                logger.warning(
                                    "Gmail historyId %s is stale or expired (HTTP 404). Falling back to full synchronization.",
                                    last_history_id,
                                )
                                # Fall through to full synchronization!
                        except Exception as h_exc:
                            logger.warning("Incremental history sync failed: %s. Falling back to full synchronization.", h_exc)

                    # 2. Full Synchronization via /messages
                    query_parts = []
                    if folder_lower == "sent":
                        query_parts.append("in:sent")
                    elif folder_lower == "drafts":
                        query_parts.append("in:draft")
                    else:
                        query_parts.append("in:inbox")

                    if search_query:
                        query_parts.append(search_query.strip())
                    final_query = " ".join(query_parts)

                    params: Dict[str, Any] = {
                        "q": final_query,
                        "maxResults": min(max(limit, 10), 50),
                    }
                    if page_token:
                        params["pageToken"] = page_token

                    list_resp = await http_client.get(
                        f"{GMAIL_API_BASE}/messages",
                        params=params,
                        headers=headers,
                        timeout=20.0,
                    )
                    if list_resp.status_code != 200:
                        err_text = f"Failed to list messages from Gmail: {list_resp.text}"
                        if hasattr(auth_store, "update_gmail_sync_state"):
                            auth_store.update_gmail_sync_state(user_id, status="error", message=err_text)
                        return {
                            "status": "error",
                            "emails": [],
                            "count": 0,
                            "new_count": 0,
                            "message": err_text,
                        }

                    list_data = list_resp.json()
                    messages = list_data.get("messages", [])
                    next_token = list_data.get("nextPageToken")

                    fetched_emails: List[CustomerEmail] = []
                    for msg_item in messages[:limit]:
                        msg_id = msg_item["id"]
                        msg_resp = await http_client.get(
                            f"{GMAIL_API_BASE}/messages/{msg_id}",
                            params={"format": "full"},
                            headers=headers,
                            timeout=15.0,
                        )
                        if msg_resp.status_code != 200:
                            continue
                        email_obj = cls._parse_gmail_message(msg_resp.json(), user_id, folder_lower)
                        fetched_emails.append(email_obj)

                    # Retrieve fresh historyId from profile checkpoint
                    latest_history_id = None
                    try:
                        prof_resp = await http_client.get(
                            f"{GMAIL_API_BASE}/profile",
                            headers=headers,
                            timeout=10.0,
                        )
                        if prof_resp.status_code == 200:
                            latest_history_id = prof_resp.json().get("historyId")
                    except Exception:
                        pass

                    if hasattr(auth_store, "update_gmail_sync_state"):
                        auth_store.update_gmail_sync_state(
                            user_id,
                            last_history_id=latest_history_id,
                            status="success",
                            message=f"Synced {len(fetched_emails)} emails",
                        )

                    return {
                        "status": "success",
                        "emails": fetched_emails,
                        "count": len(fetched_emails),
                        "new_count": len(fetched_emails),
                        "gmail_address": gmail_address,
                        "history_id": latest_history_id,
                        "next_page_token": next_token,
                        "is_incremental": False,
                        "message": f"Successfully synced {len(fetched_emails)} emails from {gmail_address}.",
                    }

            except Exception as exc:
                logger.error("Gmail sync error for user %s: %s", user_id, exc)
                if hasattr(auth_store, "update_gmail_sync_state"):
                    auth_store.update_gmail_sync_state(user_id, status="error", message=str(exc))
                return {
                    "status": "error",
                    "emails": [],
                    "count": 0,
                    "new_count": 0,
                    "message": f"Gmail sync failure: {str(exc)}",
                }

    @classmethod
    async def fetch_user_emails(
        cls,
        user_id: str,
        auth_store: Any,
        folder: str = "inbox",
        search_query: Optional[str] = None,
        limit: int = 15,
        force_full_sync: bool = False,
    ) -> Tuple[List[CustomerEmail], Optional[str]]:
        """Fetch emails returning (emails, error_msg) for backwards compatibility."""
        res = await cls.sync_user_emails(
            user_id=user_id,
            auth_store=auth_store,
            folder=folder,
            search_query=search_query,
            limit=limit,
            force_full_sync=force_full_sync,
        )
        if res["status"] in ("success", "in_progress"):
            return res.get("emails", []), None
        return [], res.get("message")

    @classmethod
    async def send_user_reply(
        cls,
        user_id: str,
        auth_store: Any,
        to_email: str,
        subject: str,
        body_text: str,
        thread_id: Optional[str] = None,
        in_reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch real email reply from user's connected Gmail account via REST API."""
        access_token, gmail_address, err = await cls.get_valid_token(user_id, auth_store)
        if err or not access_token:
            return {"success": False, "message": err or "Not authorized with Gmail."}

        # Build RFC 2822 MIME message
        msg = MIMEMultipart()
        msg["To"] = to_email
        msg["From"] = gmail_address or "me"
        sub = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        msg["Subject"] = sub

        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to

        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        raw_bytes = msg.as_bytes()
        raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

        payload: Dict[str, Any] = {"raw": raw_b64}
        if thread_id:
            payload["threadId"] = thread_id

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as http_client:
                resp = await http_client.post(
                    f"{GMAIL_API_BASE}/messages/send",
                    json=payload,
                    headers=headers,
                    timeout=20.0,
                )
                if resp.status_code not in (200, 201):
                    logger.error("Gmail send API failed (%d): %s", resp.status_code, resp.text)
                    return {"success": False, "message": f"Gmail API error: {resp.text}"}

                send_data = resp.json()
                return {
                    "success": True,
                    "message": f"Email dispatched successfully to {to_email} from {gmail_address}.",
                    "gmail_id": send_data.get("id"),
                    "thread_id": send_data.get("threadId"),
                }
        except Exception as exc:
            logger.error("Error dispatching email via Gmail API: %s", exc)
            return {"success": False, "message": f"Failed to send email: {str(exc)}"}

    @classmethod
    async def create_or_update_user_draft(
        cls,
        user_id: str,
        auth_store: Any,
        to_email: str,
        subject: str,
        body_text: str,
        thread_id: Optional[str] = None,
        existing_draft_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save or synchronize an unsent reply draft directly into user's Google Gmail Drafts."""
        access_token, gmail_address, err = await cls.get_valid_token(user_id, auth_store)
        if err or not access_token:
            return {"success": False, "message": err or "Not authorized with Gmail."}

        msg = MIMEMultipart()
        msg["To"] = to_email
        msg["From"] = gmail_address or "me"
        sub = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        msg["Subject"] = sub
        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        raw_bytes = msg.as_bytes()
        raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

        message_obj: Dict[str, Any] = {"raw": raw_b64}
        if thread_id:
            message_obj["threadId"] = thread_id

        payload = {"message": message_obj}
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as http_client:
                if existing_draft_id:
                    # Attempt update
                    put_resp = await http_client.put(
                        f"{GMAIL_API_BASE}/drafts/{existing_draft_id}",
                        json=payload,
                        headers=headers,
                        timeout=20.0,
                    )
                    if put_resp.status_code in (200, 201):
                        d_data = put_resp.json()
                        return {
                            "success": True,
                            "gmail_draft_id": d_data.get("id", existing_draft_id),
                            "message": f"Draft synchronized with your Google Gmail Drafts ({gmail_address}).",
                        }

                # Otherwise create new draft
                post_resp = await http_client.post(
                    f"{GMAIL_API_BASE}/drafts",
                    json=payload,
                    headers=headers,
                    timeout=20.0,
                )
                if post_resp.status_code not in (200, 201):
                    logger.warning("Gmail draft creation returned status %d: %s", post_resp.status_code, post_resp.text)
                    return {"success": False, "message": f"Gmail Drafts API error: {post_resp.text}"}

                d_data = post_resp.json()
                return {
                    "success": True,
                    "gmail_draft_id": d_data.get("id"),
                    "message": f"Draft saved and synchronized to your Google Gmail Drafts folder ({gmail_address}).",
                }
        except Exception as exc:
            logger.error("Error creating/updating draft via Gmail API: %s", exc)
            return {"success": False, "message": f"Failed to save draft to Gmail: {str(exc)}"}

    @classmethod
    async def disconnect(cls, user_id: str, auth_store: Any) -> Tuple[bool, str]:
        """Revoke Gmail OAuth token with Google and remove credentials from database."""
        token_info = auth_store.get_gmail_token(user_id)
        if token_info:
            token_to_revoke = token_info.get("refresh_token") or token_info.get("access_token")
            if token_to_revoke:
                try:
                    async with httpx.AsyncClient() as http_client:
                        await http_client.post(
                            GOOGLE_REVOKE_URL,
                            params={"token": token_to_revoke},
                            timeout=10.0,
                        )
                except Exception as exc:
                    logger.warning("Revocation request error with Google: %s", exc)

        auth_store.delete_gmail_token(user_id)
        return True, "Gmail connection disconnected and authorized token revoked."

    @staticmethod
    def _extract_body(payload: Dict[str, Any]) -> str:
        """Extract readable plain text from multipart or single-part payload."""
        if not payload:
            return ""

        body_data = payload.get("body", {})
        if "data" in body_data and body_data["data"]:
            try:
                raw_bytes = base64.urlsafe_b64decode(body_data["data"] + "==")
                return raw_bytes.decode("utf-8", errors="replace")
            except Exception:
                pass

        # Check parts recursively
        parts = payload.get("parts", [])
        for part in parts:
            mime_type = part.get("mimeType", "")
            if mime_type == "text/plain":
                part_data = part.get("body", {}).get("data")
                if part_data:
                    try:
                        raw_bytes = base64.urlsafe_b64decode(part_data + "==")
                        return raw_bytes.decode("utf-8", errors="replace")
                    except Exception:
                        pass
            elif mime_type == "text/html":
                part_data = part.get("body", {}).get("data")
                if part_data:
                    try:
                        raw_bytes = base64.urlsafe_b64decode(part_data + "==")
                        # Fallback html string
                        return raw_bytes.decode("utf-8", errors="replace")
                    except Exception:
                        pass
            elif "parts" in part:
                nested = GmailOAuthClient._extract_body(part)
                if nested:
                    return nested

        return ""


class GmailClient:
    """IMAP/SMTP fallback client and test email simulator."""

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993
    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 587

    def __init__(
        self,
        user_email: Optional[str] = None,
        app_password: Optional[str] = None,
    ):
        self.user_email = user_email
        self.app_password = app_password

    def test_connection(
        self,
        user_email: Optional[str] = None,
        app_password: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Test IMAP authentication against Google Gmail servers."""
        email_addr = user_email or self.user_email
        pwd = app_password or self.app_password

        if not email_addr or not pwd:
            return {
                "success": False,
                "message": "Missing Gmail address or Google App Password.",
            }

        try:
            mail = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
            mail.login(email_addr, pwd)
            mail.select("INBOX")
            status, count_data = mail.search(None, "ALL")
            total_msgs = len(count_data[0].split()) if count_data and count_data[0] else 0
            mail.logout()

            return {
                "success": True,
                "message": f"Successfully connected to Gmail ({email_addr}). {total_msgs} messages in INBOX.",
                "inbox_count": total_msgs,
            }
        except Exception as exc:
            logger.warning("Gmail IMAP test failed: %s", exc)
            return {
                "success": False,
                "message": f"Gmail Authentication Error: {str(exc)}",
            }

    def fetch_inbox_emails(
        self,
        limit: int = 10,
        user_email: Optional[str] = None,
        app_password: Optional[str] = None,
    ) -> List[CustomerEmail]:
        """Fetch emails via legacy IMAP fallback."""
        email_addr = user_email or self.user_email
        pwd = app_password or self.app_password

        if not email_addr or not pwd:
            return []

        fetched_emails: List[CustomerEmail] = []
        try:
            mail = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
            mail.login(email_addr, pwd)
            mail.select("INBOX")

            status, response = mail.search(None, "ALL")
            if status != "OK" or not response[0]:
                mail.logout()
                return []

            msg_ids = response[0].split()
            recent_ids = msg_ids[-limit:]
            recent_ids.reverse()

            for m_id in recent_ids:
                status, msg_data = mail.fetch(m_id, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue

                raw_email = msg_data[0][1]
                parsed = email.message_from_bytes(raw_email)

                subject_raw, encoding = decode_header(parsed.get("Subject", "No Subject"))[0]
                if isinstance(subject_raw, bytes):
                    subject = subject_raw.decode(encoding or "utf-8", errors="replace")
                else:
                    subject = str(subject_raw)

                from_raw, encoding = decode_header(parsed.get("From", "Customer"))[0]
                if isinstance(from_raw, bytes):
                    sender = from_raw.decode(encoding or "utf-8", errors="replace")
                else:
                    sender = str(from_raw)

                sender_name = sender
                sender_email = sender
                if "<" in sender and ">" in sender:
                    sender_name = sender.split("<")[0].strip().strip('"')
                    sender_email = sender.split("<")[1].split(">")[0].strip()

                body_text = ""
                if parsed.is_multipart():
                    for part in parsed.walk():
                        c_type = part.get_content_type()
                        c_disp = str(part.get("Content-Disposition"))
                        if c_type == "text/plain" and "attachment" not in c_disp:
                            payload = part.get_payload(decode=True)
                            if payload:
                                body_text = payload.decode(errors="replace")
                                break
                else:
                    payload = parsed.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode(errors="replace")

                email_obj = CustomerEmail(
                    id=f"GMAIL-{uuid.uuid4().hex[:8].upper()}",
                    sender_name=sender_name or "Client",
                    sender_email=sender_email or "client@example.com",
                    subject=subject,
                    body=body_text.strip() or "(Empty email body)",
                    received_at=datetime.now(timezone.utc).isoformat(),
                    priority=EmailPriority.MEDIUM,
                    status=DraftStatus.UNPROCESSED,
                    is_simulated=False,
                )
                fetched_emails.append(email_obj)

            mail.logout()
        except Exception as exc:
            logger.error("Error fetching emails from Gmail IMAP: %s", exc)

        return fetched_emails

    def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        from_email: Optional[str] = None,
        app_password: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch email reply via legacy SMTP fallback."""
        sender = from_email or self.user_email
        pwd = app_password or self.app_password

        if not sender or not pwd:
            return {"success": False, "message": "Missing Gmail SMTP credentials."}

        try:
            msg = MIMEMultipart()
            msg["From"] = sender
            msg["To"] = to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body_text, "plain"))

            server = smtplib.SMTP(self.SMTP_HOST, self.SMTP_PORT)
            server.starttls()
            server.login(sender, pwd)
            server.sendmail(sender, to_email, msg.as_string())
            server.quit()

            return {"success": True, "message": f"Email dispatched to {to_email}."}
        except Exception as exc:
            return {"success": False, "message": f"SMTP Dispatch Error: {str(exc)}"}

    @staticmethod
    def generate_simulated_email(
        subject: str,
        body: str,
        sender_name: str = "Marcus Vance",
        sender_email: str = "marcus@enterprise-directory.com",
        category: Optional[EmailCategory] = None,
        user_id: Optional[str] = None,
    ) -> CustomerEmail:
        """Create simulated client email for evaluators to test workflow without Gmail account."""
        return CustomerEmail(
            id=f"SIM-{uuid.uuid4().hex[:8].upper()}",
            user_id=user_id,
            sender_name=sender_name,
            sender_email=sender_email,
            subject=subject,
            body=body,
            received_at=datetime.now(timezone.utc).isoformat(),
            category=category,
            priority=EmailPriority.HIGH if category == EmailCategory.BILLING else EmailPriority.MEDIUM,
            status=DraftStatus.UNPROCESSED,
            is_unread=True,
            is_simulated=True,
            folder="inbox",
        )
