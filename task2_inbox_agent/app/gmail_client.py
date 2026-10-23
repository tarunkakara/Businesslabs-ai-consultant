"""Real Gmail Integration Client with OAuth 2.0 REST API & Threaded Replies.

Supports:
- Per-user OAuth 2.0 authorization with automatic token refresh
- Secure encrypted token retrieval and revocation
- Real incoming email sync across Inbox, Sent, Drafts, and search queries
- Threaded reply dispatch via Gmail REST API (users.me.messages.send)
- Distinguishes real Gmail emails from simulated test emails
- Backward-compatible IMAP/SMTP fallback
"""

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
    """Manages real per-user Gmail operations via Google's REST API."""

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

        client_id = os.getenv("GOOGLE_CLIENT_ID") or GOOGLE_CLIENT_ID
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET") or GOOGLE_CLIENT_SECRET
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
    async def fetch_user_emails(
        cls,
        user_id: str,
        auth_store: Any,
        folder: str = "inbox",
        search_query: Optional[str] = None,
        limit: int = 15,
    ) -> Tuple[List[CustomerEmail], Optional[str]]:
        """Fetch incoming emails from user's authorized Gmail mailbox via REST API."""
        access_token, gmail_address, err = await cls.get_valid_token(user_id, auth_store)
        if err or not access_token:
            return [], err

        # Determine Gmail search query
        folder_lower = (folder or "inbox").lower()
        if folder_lower == "sent":
            q_label = "label:SENT"
        elif folder_lower == "drafts":
            q_label = "label:DRAFT"
        else:
            q_label = "label:INBOX"

        query_parts = [q_label]
        if search_query:
            query_parts.append(search_query.strip())
        final_query = " ".join(query_parts)

        headers = {"Authorization": f"Bearer {access_token}"}
        fetched_emails: List[CustomerEmail] = []

        try:
            async with httpx.AsyncClient() as http_client:
                # 1. List messages matching query
                list_resp = await http_client.get(
                    f"{GMAIL_API_BASE}/messages",
                    params={"q": final_query, "maxResults": min(limit, 30)},
                    headers=headers,
                    timeout=20.0,
                )
                if list_resp.status_code != 200:
                    return [], f"Failed to list messages from Gmail: {list_resp.text}"

                list_data = list_resp.json()
                messages = list_data.get("messages", [])
                if not messages:
                    return [], None

                # 2. Fetch full message details for each
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

                    msg_data = msg_resp.json()
                    thread_id = msg_data.get("threadId")
                    label_ids = msg_data.get("labelIds", [])
                    is_unread = "UNREAD" in label_ids

                    # Extract headers
                    payload = msg_data.get("payload", {})
                    p_headers = payload.get("headers", [])
                    header_map = {h["name"].lower(): h["value"] for h in p_headers if "name" in h and "value" in h}

                    subject = header_map.get("subject", "No Subject")
                    from_val = header_map.get("from", "Unknown Sender")
                    date_val = header_map.get("date", datetime.now(timezone.utc).isoformat())

                    # Parse sender name and email
                    sender_name = from_val
                    sender_email = from_val
                    if "<" in from_val and ">" in from_val:
                        sender_name = from_val.split("<")[0].strip().strip('"')
                        sender_email = from_val.split("<")[1].split(">")[0].strip()

                    # Extract body text
                    body_text = cls._extract_body(payload)

                    email_obj = CustomerEmail(
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
                    fetched_emails.append(email_obj)

            return fetched_emails, None
        except Exception as exc:
            logger.error("Gmail fetch error: %s", exc)
            return [], str(exc)

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
