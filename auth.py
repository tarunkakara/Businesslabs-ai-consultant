"""Secure Authentication, Password Hashing, Validation, and Session Management.

Enforces:
- Normalized lowercase emails with RFC-compliant validation
- Strong password complexity (min 8 chars, uppercase, lowercase, digit, special char)
- Salted PBKDF2-HMAC-SHA256 password hashing (zero plain-text storage)
- Cryptographically signed tamper-proof session tokens
- Automatic demo account seeding with valid credentials
"""

import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("monorepo.auth")

BASE_DIR = Path(__file__).resolve().parent
AUTH_DB_PATH = BASE_DIR / "data" / "auth.db"
AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SESSION_SECRET = os.getenv("SESSION_SECRET", "businesslabs-ai-consultant-sec-auth-token-2026")
SESSION_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days

EMAIL_REGEX = re.compile(r"^[a-z0-9_.+-]+@[a-z0-9-]+\.[a-z0-9-.]+$")
SPECIAL_CHARS = r"@#$%!^&*()_+\-=\[\]{}|;:,.<>?"


def normalize_email(email_str: str) -> str:
    """Normalize email to strictly stripped lowercase string."""
    if not email_str:
        return ""
    return email_str.strip().lower()


def validate_email(email_str: str) -> Tuple[bool, str, Optional[str]]:
    """Validate email format and return (is_valid, normalized_email, error_message)."""
    norm = normalize_email(email_str)
    if not norm:
        return False, "", "Email address cannot be empty."
    if len(norm) > 254:
        return False, norm, "Email address exceeds maximum length."
    if not EMAIL_REGEX.match(norm):
        return False, norm, "Invalid email format. Please provide a valid address (e.g. user@example.com)."
    return True, norm, None


def validate_password(password: str) -> Tuple[bool, List[str]]:
    """Validate password against complexity rules."""
    errors = []
    if not password or len(password) < 8:
        errors.append("Password must be at least 8 characters long.")
    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least one uppercase letter (A-Z).")
    if not re.search(r"[a-z]", password):
        errors.append("Password must contain at least one lowercase letter (a-z).")
    if not re.search(r"[0-9]", password):
        errors.append("Password must contain at least one number (0-9).")
    if not re.search(rf"[{re.escape(SPECIAL_CHARS)}]", password):
        errors.append("Password must contain at least one special character (@, #, $, %, !, etc.).")
    return len(errors) == 0, errors


def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """Generate salted PBKDF2-HMAC-SHA256 hash."""
    if not salt:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=120000,
    )
    return key.hex(), salt


def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    """Safely verify candidate password against stored PBKDF2 hash."""
    candidate_hash, _ = hash_password(password, stored_salt)
    return secrets.compare_digest(candidate_hash, stored_hash)


class AuthStore:
    """SQLite-backed user storage and session validation."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or AUTH_DB_PATH
        self._init_db()
        self._seed_demo_user()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create users table with unique lowercase index on email."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    role TEXT DEFAULT 'Technical Consultant',
                    created_at REAL NOT NULL
                )
            """)
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_email ON users(email)")
            conn.commit()

    def _seed_demo_user(self) -> None:
        """Seed default enterprise consultant demo accounts if absent."""
        seed_accounts = [
            ("consultant@businesslabs.ai", "Consultant@123", "Sarah Connor", "Lead AI Tech Consultant"),
            ("lead@businesslabs.ai", "Consultant@123", "Lead Consultant", "Enterprise Architect"),
        ]
        for demo_email, demo_pwd, name, role in seed_accounts:
            existing = self.get_user_by_email(demo_email)
            if not existing:
                pwd_hash, salt = hash_password(demo_pwd)
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO users (id, email, password_hash, salt, full_name, role, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        secrets.token_hex(8),
                        demo_email,
                        pwd_hash,
                        salt,
                        name,
                        role,
                        time.time(),
                    ))
                    conn.commit()
                logger.info("Seeded demo account (%s).", demo_email)

    def register_user(self, email: str, password: str, full_name: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Validate and create new user record."""
        is_valid_email, norm_email, err_email = validate_email(email)
        if not is_valid_email:
            return False, None, err_email

        is_valid_pwd, pwd_errors = validate_password(password)
        if not is_valid_pwd:
            return False, None, " ".join(pwd_errors)

        name = full_name.strip() or "Consultant"
        if len(name) < 2:
            return False, None, "Full name must be at least 2 characters."

        # Check existing
        if self.get_user_by_email(norm_email):
            return False, None, "An account with this email address already exists. Please sign in."

        user_id = secrets.token_hex(8)
        pwd_hash, salt = hash_password(password)
        now = time.time()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (id, email, password_hash, salt, full_name, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, norm_email, pwd_hash, salt, name, "Technical Consultant", now))
            conn.commit()

        user_dict = {
            "id": user_id,
            "email": norm_email,
            "full_name": name,
            "role": "Technical Consultant",
        }
        return True, user_dict, "Registration successful."

    def authenticate_user(self, email: str, password: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Authenticate user credentials."""
        norm_email = normalize_email(email)
        if not norm_email or not password:
            return False, None, "Email and password are required."

        user = self.get_user_by_email(norm_email)
        if not user:
            return False, None, "Invalid email or password."

        if not verify_password(password, user["password_hash"], user["salt"]):
            return False, None, "Invalid email or password."

        user_dict = {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
        }
        return True, user_dict, "Login successful."

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        norm_email = normalize_email(email)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE email = ?", (norm_email,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None


# Token Management
def create_session_token(user_dict: Dict[str, Any]) -> str:
    """Create tamper-proof HMAC-SHA256 signed session token."""
    payload = {
        "user_id": user_dict["id"],
        "email": user_dict["email"],
        "full_name": user_dict.get("full_name", "Consultant"),
        "role": user_dict.get("role", "Consultant"),
        "exp": int(time.time() + SESSION_EXPIRY_SECONDS),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
    sig = hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_session_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Verify signature and expiration of session token."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig = token.split(".", 1)
        expected_sig = hmac.new(SESSION_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(sig, expected_sig):
            return None

        # Add padding back if necessary
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded.encode("utf-8"))
        payload = json.loads(payload_bytes.decode("utf-8"))

        if payload.get("exp", 0) < time.time():
            return None

        return payload
    except Exception as exc:
        logger.debug("Session verification failure: %s", exc)
        return None
