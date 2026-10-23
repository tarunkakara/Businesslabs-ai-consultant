"""Secure Authentication, Password Hashing, Validation, and Session Management.

Enforces:
- Normalized lowercase emails with RFC-compliant validation
- Strong password complexity (min 8 chars, uppercase, lowercase, digit, special char)
- Bcrypt password hashing (12 salt rounds, zero plain-text storage)
- Cryptographically signed tamper-proof session tokens
- Database-backed active session tracking with instant server-side revocation on logout
- Brute-force protection with sliding-window rate limiting
- Real Google OAuth integration support
- Zero hardcoded demo accounts or backdoor bypasses
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
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import bcrypt
from cryptography.fernet import Fernet

logger = logging.getLogger("monorepo.auth")

BASE_DIR = Path(__file__).resolve().parent
AUTH_DB_PATH = Path(os.getenv("AUTH_DB_PATH", str(BASE_DIR / "data" / "auth.db")))
AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SESSION_SECRET = os.getenv("SESSION_SECRET", "businesslabs-ai-consultant-sec-auth-token-2026")
SESSION_EXPIRY_SECONDS = int(os.getenv("SESSION_EXPIRY_SECONDS", str(7 * 24 * 3600)))  # 7 days

EMAIL_REGEX = re.compile(r"^[a-z0-9_.+-]+@[a-z0-9-]+\.[a-z0-9-.]+$")
SPECIAL_CHARS = r"@#$%!^&*()_+\-=\[\]{}|;:,.<>?"

# Rate limiting settings: 5 failed attempts within 300 seconds (5 min)
RATE_LIMIT_MAX_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 300


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
    """Validate password against complexity rules without altering case."""
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


def hash_password(password: str) -> str:
    """Hash password using modern bcrypt with 12 salt rounds."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    """Safely verify candidate password against stored bcrypt hash."""
    if not password or not stored_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
    except Exception as exc:
        logger.error("Bcrypt verification error: %s", exc)
        return False


class TokenCrypt:
    """Fernet authenticated symmetric encryption for OAuth credentials at rest."""

    @classmethod
    def _get_fernet(cls) -> Fernet:
        secret = os.getenv("TOKEN_ENCRYPTION_KEY") or SESSION_SECRET
        key_32 = hashlib.sha256(secret.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(key_32))

    @classmethod
    def encrypt(cls, plain_text: Optional[str]) -> Optional[str]:
        if not plain_text:
            return None
        return cls._get_fernet().encrypt(plain_text.encode("utf-8")).decode("utf-8")

    @classmethod
    def decrypt(cls, cipher_text: Optional[str]) -> Optional[str]:
        if not cipher_text:
            return None
        try:
            return cls._get_fernet().decrypt(cipher_text.encode("utf-8")).decode("utf-8")
        except Exception as exc:
            logger.error("Token decryption failure: %s", exc)
            return None


class RateLimiter:
    """Thread-safe sliding-window rate limiter for brute-force protection."""

    def __init__(self, max_attempts: int = RATE_LIMIT_MAX_ATTEMPTS, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def is_rate_limited(self, *keys: str) -> bool:
        now = time.time()
        with self._lock:
            for key in keys:
                if not key:
                    continue
                attempts = [t for t in self._attempts.get(key, []) if now - t < self.window_seconds]
                self._attempts[key] = attempts
                if len(attempts) >= self.max_attempts:
                    return True
        return False

    def record_failure(self, *keys: str) -> None:
        now = time.time()
        with self._lock:
            for key in keys:
                if not key:
                    continue
                if key not in self._attempts:
                    self._attempts[key] = []
                self._attempts[key].append(now)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                if key in self._attempts:
                    self._attempts.pop(key, None)


class AuthStore:
    """Secure user and active session store with zero demo accounts."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or AUTH_DB_PATH
        self.rate_limiter = RateLimiter()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize tables for users and active sessions."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    role TEXT DEFAULT 'Technical Consultant',
                    auth_provider TEXT DEFAULT 'local',
                    google_id TEXT,
                    avatar_url TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_email ON users(email)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS active_sessions (
                    session_token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_token ON active_sessions(session_token)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_user ON active_sessions(user_id)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_gmail_tokens (
                    user_id TEXT PRIMARY KEY,
                    gmail_address TEXT NOT NULL,
                    access_token_enc TEXT NOT NULL,
                    refresh_token_enc TEXT,
                    token_expiry REAL NOT NULL,
                    scope TEXT NOT NULL,
                    connected_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_gmail_user ON user_gmail_tokens(user_id)")
            conn.commit()

    def register_user(self, email: str, password: str, full_name: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """Validate and create new user record with bcrypt hashing."""
        is_valid_email, norm_email, err_email = validate_email(email)
        if not is_valid_email:
            return False, None, err_email

        is_valid_pwd, pwd_errors = validate_password(password)
        if not is_valid_pwd:
            return False, None, " ".join(pwd_errors)

        name = full_name.strip()
        if len(name) < 2:
            return False, None, "Full name must be at least 2 characters."

        # Check existing email
        if self.get_user_by_email(norm_email):
            return False, None, "An account with this email address already exists. Please sign in."

        user_id = secrets.token_hex(12)
        pwd_hash = hash_password(password)
        now = time.time()
        avatar = f"https://api.dicebear.com/7.x/initials/svg?seed={name}&backgroundColor=4f46e5,7c3aed"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (id, email, password_hash, full_name, role, auth_provider, google_id, avatar_url, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'local', NULL, ?, ?, ?)
            """, (user_id, norm_email, pwd_hash, name, "Technical Consultant", avatar, now, now))
            conn.commit()

        user_dict = {
            "id": user_id,
            "email": norm_email,
            "full_name": name,
            "role": "Technical Consultant",
            "avatar_url": avatar,
            "auth_provider": "local",
        }
        return True, user_dict, "Registration successful."

    def authenticate_user(
        self, email: str, password: str, client_ip: str = "unknown"
    ) -> Tuple[bool, Optional[Dict[str, Any]], str, int]:
        """Authenticate user credentials with rate-limiting and generic security errors."""
        norm_email = normalize_email(email)
        keys = [k for k in [client_ip, norm_email] if k and k != "unknown"]

        if self.rate_limiter.is_rate_limited(*keys):
            return False, None, "Too many failed login attempts. Please try again in 5 minutes.", 429

        generic_error = "Incorrect email or password. Please try again."

        if not norm_email or not password:
            self.rate_limiter.record_failure(*keys)
            return False, None, generic_error, 401

        user = self.get_user_by_email(norm_email)
        if not user:
            self.rate_limiter.record_failure(*keys)
            return False, None, generic_error, 401

        if not verify_password(password, user["password_hash"]):
            self.rate_limiter.record_failure(*keys)
            return False, None, generic_error, 401

        # Clear failed attempts on success
        self.rate_limiter.reset(*keys)

        user_dict = {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "avatar_url": user.get("avatar_url") or f"https://api.dicebear.com/7.x/initials/svg?seed={user['full_name']}&backgroundColor=4f46e5,7c3aed",
            "auth_provider": user.get("auth_provider", "local"),
        }
        return True, user_dict, "Login successful.", 200

    def upsert_oauth_user(
        self, email: str, full_name: str, google_id: str, avatar_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create or update account authenticated via Google OAuth."""
        norm_email = normalize_email(email)
        name = full_name.strip() or norm_email.split("@")[0]
        avatar = avatar_url or f"https://api.dicebear.com/7.x/initials/svg?seed={name}&backgroundColor=4f46e5,7c3aed"
        now = time.time()

        user = self.get_user_by_email(norm_email)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if user:
                user_id = user["id"]
                cursor.execute("""
                    UPDATE users
                    SET full_name = ?, google_id = ?, avatar_url = ?, updated_at = ?
                    WHERE id = ?
                """, (name, google_id, avatar, now, user_id))
            else:
                user_id = secrets.token_hex(12)
                # Unusable password hash for OAuth-only user
                dummy_pwd_hash = hash_password(secrets.token_urlsafe(32))
                cursor.execute("""
                    INSERT INTO users (id, email, password_hash, full_name, role, auth_provider, google_id, avatar_url, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'google', ?, ?, ?, ?)
                """, (user_id, norm_email, dummy_pwd_hash, name, "Technical Consultant", google_id, avatar, now, now))
            conn.commit()

        return {
            "id": user_id,
            "email": norm_email,
            "full_name": name,
            "role": "Technical Consultant",
            "avatar_url": avatar,
            "auth_provider": "google",
        }

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

    # Server-Side Session Invalidation
    def create_session(self, user_dict: Dict[str, Any]) -> str:
        """Create signed session token and record in active_sessions table."""
        token = create_session_token(user_dict)
        now = time.time()
        exp = now + SESSION_EXPIRY_SECONDS
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO active_sessions (session_token, user_id, email, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (token, user_dict["id"], user_dict["email"], now, exp))
            conn.commit()
        return token

    def revoke_session(self, token: Optional[str]) -> bool:
        """Invalidate session in database immediately on logout."""
        if not token:
            return False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM active_sessions WHERE session_token = ?", (token,))
            conn.commit()
        return True

    def validate_session(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """Verify token cryptographic signature and ensure active in database."""
        payload = verify_session_token(token)
        if not payload:
            return None

        # Check against server-side active_sessions table
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, expires_at FROM active_sessions WHERE session_token = ?", (token,))
            row = cursor.fetchone()
            if not row:
                return None
            if row["expires_at"] < time.time():
                # Expired: clean up
                cursor.execute("DELETE FROM active_sessions WHERE session_token = ?", (token,))
                conn.commit()
                return None

        user = self.get_user_by_id(payload["user_id"])
        if not user:
            return None

        return {
            "id": user["id"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user.get("role", "Technical Consultant"),
            "avatar_url": user.get("avatar_url") or f"https://api.dicebear.com/7.x/initials/svg?seed={user['full_name']}&backgroundColor=4f46e5,7c3aed",
            "auth_provider": user.get("auth_provider", "local"),
        }

    # User Gmail OAuth Token Management (Encrypted at Rest)
    def save_gmail_token(
        self,
        user_id: str,
        gmail_address: str,
        access_token: str,
        refresh_token: Optional[str],
        expires_in: int,
        scope: str = "",
    ) -> None:
        """Store encrypted Gmail OAuth credentials associated with a user."""
        now = time.time()
        expiry = now + int(expires_in)
        enc_access = TokenCrypt.encrypt(access_token)
        enc_refresh = TokenCrypt.encrypt(refresh_token) if refresh_token else None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # If refresh_token is omitted on re-auth, preserve existing encrypted refresh token
            if not refresh_token:
                cursor.execute("SELECT refresh_token_enc FROM user_gmail_tokens WHERE user_id = ?", (user_id,))
                existing = cursor.fetchone()
                if existing and existing["refresh_token_enc"]:
                    enc_refresh = existing["refresh_token_enc"]

            cursor.execute("""
                INSERT OR REPLACE INTO user_gmail_tokens
                (user_id, gmail_address, access_token_enc, refresh_token_enc, token_expiry, scope, connected_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, gmail_address.lower().strip(), enc_access, enc_refresh, expiry, scope, now, now))
            conn.commit()

    def get_gmail_token(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve decrypted Gmail OAuth credentials for a specific user."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_gmail_tokens WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if not row:
                return None

            return {
                "user_id": row["user_id"],
                "gmail_address": row["gmail_address"],
                "access_token": TokenCrypt.decrypt(row["access_token_enc"]),
                "refresh_token": TokenCrypt.decrypt(row["refresh_token_enc"]),
                "token_expiry": row["token_expiry"],
                "scope": row["scope"],
                "connected_at": row["connected_at"],
                "is_expired": time.time() >= (row["token_expiry"] - 60),
            }

    def update_gmail_access_token(self, user_id: str, new_access_token: str, expires_in: int) -> None:
        """Update access token and expiry after a token refresh."""
        now = time.time()
        expiry = now + int(expires_in)
        enc_access = TokenCrypt.encrypt(new_access_token)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE user_gmail_tokens
                SET access_token_enc = ?, token_expiry = ?, updated_at = ?
                WHERE user_id = ?
            """, (enc_access, expiry, now, user_id))
            conn.commit()

    def delete_gmail_token(self, user_id: str) -> bool:
        """Remove Gmail connection for a user."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_gmail_tokens WHERE user_id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0


# Cryptographic Token Management
def create_session_token(user_dict: Dict[str, Any]) -> str:
    """Create tamper-proof HMAC-SHA256 signed session token."""
    payload = {
        "user_id": user_dict["id"],
        "email": user_dict["email"],
        "full_name": user_dict.get("full_name", ""),
        "role": user_dict.get("role", "Technical Consultant"),
        "nonce": secrets.token_hex(8),
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

        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded.encode("utf-8"))
        payload = json.loads(payload_bytes.decode("utf-8"))

        if payload.get("exp", 0) < time.time():
            return None

        return payload
    except Exception as exc:
        logger.debug("Session verification failure: %s", exc)
        return None
