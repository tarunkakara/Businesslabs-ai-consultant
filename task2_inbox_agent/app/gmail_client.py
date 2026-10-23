"""Real Gmail Integration Client using Python's imaplib and smtplib.

Enables bi-directional synchronization with real Gmail accounts:
- Fetch incoming emails via IMAP (imap.gmail.com:993 SSL)
- Dispatch approved email replies via SMTP (smtp.gmail.com:587 STARTTLS)
- Interactive test simulator for live evaluation without requiring immediate Google credentials.
"""

import email
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import imaplib
import logging
import smtplib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from task2_inbox_agent.app.models import CustomerEmail, DraftStatus, EmailCategory, EmailPriority

logger = logging.getLogger("inbox_agent.gmail")


class GmailClient:
    """Manages IMAP and SMTP connections to Google Mail."""

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
            # Test IMAP
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
        """Fetch real recent emails from Gmail INBOX over IMAP."""
        email_addr = user_email or self.user_email
        pwd = app_password or self.app_password

        if not email_addr or not pwd:
            logger.info("No Gmail credentials configured; returning empty list.")
            return []

        fetched_emails: List[CustomerEmail] = []
        try:
            mail = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
            mail.login(email_addr, pwd)
            mail.select("INBOX")

            # Search recent emails
            status, response = mail.search(None, "ALL")
            if status != "OK" or not response[0]:
                mail.logout()
                return []

            msg_ids = response[0].split()
            # Take newest `limit` messages
            recent_ids = msg_ids[-limit:]
            recent_ids.reverse()

            for m_id in recent_ids:
                status, msg_data = mail.fetch(m_id, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue

                raw_email = msg_data[0][1]
                parsed = email.message_from_bytes(raw_email)

                # Decode subject
                subject_raw, encoding = decode_header(parsed.get("Subject", "No Subject"))[0]
                if isinstance(subject_raw, bytes):
                    subject = subject_raw.decode(encoding or "utf-8", errors="replace")
                else:
                    subject = str(subject_raw)

                # Decode From
                from_raw, encoding = decode_header(parsed.get("From", "Customer"))[0]
                if isinstance(from_raw, bytes):
                    sender = from_raw.decode(encoding or "utf-8", errors="replace")
                else:
                    sender = str(from_raw)

                # Extract sender name and email
                sender_name = sender
                sender_email = sender
                if "<" in sender and ">" in sender:
                    sender_name = sender.split("<")[0].strip().strip('"')
                    sender_email = sender.split("<")[1].split(">")[0].strip()

                # Extract body
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
        """Dispatch real email reply via Gmail SMTP."""
        sender = from_email or self.user_email
        pwd = app_password or self.app_password

        if not sender or not pwd:
            return {
                "success": False,
                "message": "Missing Gmail SMTP credentials.",
            }

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

            logger.info("Successfully dispatched email via Gmail SMTP to %s", to_email)
            return {
                "success": True,
                "message": f"Email successfully dispatched to {to_email} via real Gmail SMTP.",
            }
        except Exception as exc:
            logger.error("Gmail SMTP dispatch error: %s", exc)
            return {
                "success": False,
                "message": f"SMTP Dispatch Error: {str(exc)}",
            }

    @staticmethod
    def generate_simulated_email(
        subject: str,
        body: str,
        sender_name: str = "Marcus Vance",
        sender_email: str = "marcus@enterprise-directory.com",
        category: Optional[EmailCategory] = None,
    ) -> CustomerEmail:
        """Create simulated client email for evaluators to test workflow without Gmail account."""
        return CustomerEmail(
            id=f"SIM-{uuid.uuid4().hex[:8].upper()}",
            sender_name=sender_name,
            sender_email=sender_email,
            subject=subject,
            body=body,
            received_at=datetime.now(timezone.utc).isoformat(),
            category=category,
            priority=EmailPriority.HIGH if category == EmailCategory.BILLING else EmailPriority.MEDIUM,
            status=DraftStatus.UNPROCESSED,
        )
