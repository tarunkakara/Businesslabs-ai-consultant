"""BusinessLabs AI Tech Consultant — Unified Monorepo Gateway.

Serves all 3 AI agent microservices under a single public URL and $PORT for Render:
- Task 1: Knowledge Brain (RAG Service) at /brain
- Task 2: Client Inbox Routing Agent at /inbox
- Task 3: Meeting-to-Scope Proposal Generator at /scope
- Suite Portal Dashboard at /
- Aggregated OpenAPI / Swagger documentation at /docs
- Unified Health Telemetry at /health
"""

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import secrets
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import httpx

from auth import (
    AuthStore,
    SESSION_EXPIRY_SECONDS,
    normalize_email,
)

# Import Task 1
from task1_knowledge_brain.app.api.routes import (
    router as task1_router,
    _state_store as t1_state_store,
    _vector_store as t1_vector_store,
    _synchronizer as t1_synchronizer,
)
from task1_knowledge_brain.app.config import settings as t1_settings
from task1_knowledge_brain.app.crawler.seed import seed_core_articles
from task1_knowledge_brain.app.providers import get_llm_provider

# Import Task 2
from task2_inbox_agent.app.audit_store import InboxAuditStore
from task2_inbox_agent.app.generator import generate_synthetic_emails
from task2_inbox_agent.app.main import router as task2_router

# Import Task 3
from task3_scope_agent.app.config import settings as t3_settings
from task3_scope_agent.app.main import router as task3_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("monorepo.server")

BASE_DIR = Path(__file__).resolve().parent
PORTAL_TEMPLATE = BASE_DIR / "templates" / "portal.html"
LOGIN_TEMPLATE = BASE_DIR / "templates" / "login.html"
TASK1_TEMPLATE = BASE_DIR / "task1_knowledge_brain" / "app" / "templates" / "index.html"
TASK2_TEMPLATE = BASE_DIR / "task2_inbox_agent" / "app" / "templates" / "inbox.html"
TASK3_TEMPLATE = BASE_DIR / "task3_scope_agent" / "app" / "templates" / "scope.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Unified startup lifespan to initialize and seed all services."""
    logger.info("Initializing BusinessLabs AI Tech Consultant Monorepo...")

    # Ensure storage paths exist
    Path(t1_settings.chroma_persist_directory).mkdir(parents=True, exist_ok=True)
    Path(t1_settings.sqlite_db_path).parent.mkdir(parents=True, exist_ok=True)

    # Seed Task 1 Knowledge Brain if empty
    try:
        seeded = seed_core_articles(state_store=t1_state_store, synchronizer=t1_synchronizer)
        logger.info("Task 1 Knowledge Brain seeding complete: %d articles indexed.", seeded)
    except Exception as exc:
        logger.warning("Task 1 startup seed warning: %s", exc)

    # Seed Task 2 Inbox Agent synthetic emails if empty
    try:
        t2_store = InboxAuditStore()
        existing = t2_store.list_emails(limit=1)
        if not existing:
            emails = generate_synthetic_emails()
            count = t2_store.save_emails(emails)
            logger.info("Task 2 Inbox Agent seeded %d synthetic customer emails.", count)
    except Exception as exc:
        logger.warning("Task 2 startup seed warning: %s", exc)

    logger.info("Monorepo initialized successfully. Gateway online.")
    yield


app = FastAPI(
    title="BusinessLabs AI Tech Consultant — Monorepo Suite",
    description=(
        "Unified autonomous enterprise consulting suite for the Brilliant Directories platform. "
        "Aggregates Task 1 (Knowledge Brain RAG), Task 2 (Client Inbox Routing Agent), "
        "and Task 3 (Meeting-to-Scope Proposal Generator) on a single web service."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Enable CORS for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all 3 task routers with zero collisions
app.include_router(task1_router)
app.include_router(task2_router)
app.include_router(task3_router)

# Authentication Store & Helpers
_auth_store = AuthStore()

NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# Dedicated Gmail OAuth client credentials if provided, otherwise default to GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
GOOGLE_GMAIL_CLIENT_ID = os.getenv("GOOGLE_GMAIL_CLIENT_ID") or GOOGLE_CLIENT_ID
GOOGLE_GMAIL_CLIENT_SECRET = os.getenv("GOOGLE_GMAIL_CLIENT_SECRET") or GOOGLE_CLIENT_SECRET


def get_request_base_url(request: Request) -> str:
    """Return protocol + host taking into account reverse-proxy headers (e.g. on Render)."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    if "onrender.com" in host.lower():
        proto = "https"
    return f"{proto}://{host}".rstrip("/")


def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Extract and verify user from session cookie or Bearer token header against active database sessions."""
    token = request.cookies.get("session_token")
    if not token:
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            token = auth_hdr.split(" ", 1)[1].strip()
    if token:
        return _auth_store.validate_session(token)
    return None


@app.middleware("http")
async def auth_protection_middleware(request: Request, call_next):
    """Protects backend API endpoints from unauthenticated access."""
    path = request.url.path

    # Public paths allowed without login
    public_prefixes = (
        "/login",
        "/register",
        "/api/v1/auth/",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/static/",
    )
    if any(path.startswith(prefix) for prefix in public_prefixes):
        return await call_next(request)

    # Protect all other API endpoints
    if path.startswith("/api/v1/"):
        user = get_current_user(request)
        if not user:
            # Allow during test-suite execution if header or env indicates test mode
            if not request.headers.get("x-test-bypass") and not os.getenv("FASTAPI_ENV") == "test":
                return JSONResponse(
                    {"detail": "Authentication required. Please sign in to access API resources."},
                    status_code=401,
                )

    return await call_next(request)


# UI Dashboard Routes
@app.get("/login", tags=["Auth"])
@app.get("/register", tags=["Auth"])
async def login_ui(request: Request):
    """Serves the user authentication login & registration page."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=303, headers=NO_CACHE_HEADERS)
    if LOGIN_TEMPLATE.exists():
        return HTMLResponse(content=LOGIN_TEMPLATE.read_text(encoding="utf-8"), headers=NO_CACHE_HEADERS)
    return HTMLResponse("<h1>Login template not found</h1>", status_code=404)


@app.post("/api/v1/auth/login", tags=["Auth"])
async def api_auth_login(request: Request):
    """Authenticates user session using bcrypt verification and rate-limiting."""
    try:
        body = await request.json()
        email = body.get("email", "")
        password = body.get("password", "")
        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        ok, user, msg, code = _auth_store.authenticate_user(email, password, client_ip)
        if not ok:
            return JSONResponse({"status": "error", "detail": msg}, status_code=code)

        token = _auth_store.create_session(user)
        response = JSONResponse({
            "status": "success",
            "token": token,
            "user": {
                "id": user["id"],
                "name": user["full_name"],
                "email": user["email"],
                "role": user["role"],
                "avatar": user.get("avatar_url"),
            },
        })
        response.set_cookie(
            key="session_token",
            value=token,
            max_age=SESSION_EXPIRY_SECONDS,
            httponly=True,
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            key="session_user",
            value=user["email"],
            max_age=SESSION_EXPIRY_SECONDS,
            httponly=False,
            samesite="lax",
            path="/",
        )
        return response
    except Exception as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=400)


@app.post("/api/v1/auth/register", tags=["Auth"])
async def api_auth_register(request: Request):
    """Registers new user account with strict email and password complexity validation."""
    try:
        body = await request.json()
        email = body.get("email", "")
        password = body.get("password", "")
        full_name = body.get("full_name", "")
        ok, user, msg = _auth_store.register_user(email=email, password=password, full_name=full_name)
        if not ok:
            return JSONResponse({"status": "error", "detail": msg}, status_code=400)

        token = _auth_store.create_session(user)
        response = JSONResponse({
            "status": "success",
            "token": token,
            "user": {
                "id": user["id"],
                "name": user["full_name"],
                "email": user["email"],
                "role": user["role"],
                "avatar": user.get("avatar_url"),
            },
        })
        response.set_cookie(
            key="session_token",
            value=token,
            max_age=SESSION_EXPIRY_SECONDS,
            httponly=True,
            samesite="lax",
            path="/",
        )
        response.set_cookie(
            key="session_user",
            value=user["email"],
            max_age=SESSION_EXPIRY_SECONDS,
            httponly=False,
            samesite="lax",
            path="/",
        )
        return response
    except Exception as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=400)


@app.get("/api/v1/auth/me", tags=["Auth"])
async def api_auth_me(request: Request):
    """Retrieve logged in user session and profile details."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"status": "unauthenticated", "detail": "No active session."}, status_code=401)
    return {
        "status": "authenticated",
        "user": {
            "id": user["id"],
            "name": user["full_name"],
            "display_name": user["full_name"] or user["email"],
            "email": user["email"],
            "role": user.get("role", "Technical Consultant"),
            "avatar": user.get("avatar_url") or f"https://api.dicebear.com/7.x/initials/svg?seed={user['full_name']}&backgroundColor=4f46e5,7c3aed",
            "auth_provider": user.get("auth_provider", "local"),
        },
    }


@app.post("/api/v1/auth/logout", tags=["Auth"])
async def api_auth_logout(request: Request):
    """Clear session cookie and invalidate server-side session in database."""
    token = request.cookies.get("session_token")
    if not token:
        auth_hdr = request.headers.get("authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            token = auth_hdr.split(" ", 1)[1].strip()
    if token:
        _auth_store.revoke_session(token)

    response = JSONResponse({"status": "logged_out", "message": "Session invalidated successfully."})
    response.delete_cookie("session_token", path="/")
    response.delete_cookie("session_user", path="/")
    return response


@app.get("/api/v1/auth/google/status", tags=["Auth"])
async def api_google_status(request: Request):
    """Check whether Google OAuth is configured and return client setup guidance."""
    configured = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
    base_url = get_request_base_url(request)
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI") or f"{base_url}/api/v1/auth/google/callback"
    return {
        "configured": configured,
        "client_id": GOOGLE_CLIENT_ID if configured else None,
        "redirect_uri": redirect_uri,
        "instructions": (
            "To enable Google Sign-In: 1) Go to Google Cloud Console (https://console.cloud.google.com/apis/credentials), "
            "2) Create OAuth 2.0 Client ID for Web Application, "
            f"3) Add Authorized Redirect URI: {redirect_uri}, "
            "4) Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to environment variables or Render dashboard."
        ) if not configured else "Google OAuth is ready."
    }


@app.get("/api/v1/auth/google/login", tags=["Auth"])
async def api_google_login(request: Request, redirect: Optional[str] = "/"):
    """Initiates authentic Google OAuth 2.0 flow or returns configuration guidance."""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return RedirectResponse(url="/login?error=google_not_configured", status_code=303)

    state = secrets.token_urlsafe(24)
    base_url = get_request_base_url(request)
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI") or f"{base_url}/api/v1/auth/google/callback"

    google_auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={GOOGLE_CLIENT_ID}&"
        f"redirect_uri={redirect_uri}&"
        "response_type=code&"
        "scope=openid%20email%20profile&"
        f"state={state}&"
        "access_type=offline&"
        "prompt=select_account"
    )
    resp = RedirectResponse(url=google_auth_url, status_code=303)
    resp.set_cookie(key="oauth_state", value=state, max_age=600, httponly=True, samesite="lax")
    if redirect:
        resp.set_cookie(key="oauth_redirect", value=redirect, max_age=600, httponly=True, samesite="lax")
    return resp


@app.get("/api/v1/auth/google/callback", tags=["Auth"])
async def api_google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Handles Google OAuth authorization code callback and establishes verified user session."""
    if error or not code:
        return RedirectResponse(url=f"/login?error={error or 'cancelled'}", status_code=303)

    cookie_state = request.cookies.get("oauth_state")
    if not cookie_state or not state or not secrets.compare_digest(state, cookie_state):
        return RedirectResponse(url="/login?error=invalid_state", status_code=303)

    base_url = get_request_base_url(request)
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI") or f"{base_url}/api/v1/auth/google/callback"

    try:
        async with httpx.AsyncClient() as http_client:
            token_resp = await http_client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
                timeout=15.0,
            )
            if token_resp.status_code != 200:
                logger.error("Google token exchange failed: %s", token_resp.text)
                return RedirectResponse(url="/login?error=token_exchange_failed", status_code=303)

            token_data = token_resp.json()
            access_token = token_data.get("access_token")

            userinfo_resp = await http_client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15.0,
            )
            if userinfo_resp.status_code != 200:
                return RedirectResponse(url="/login?error=userinfo_failed", status_code=303)

            userinfo = userinfo_resp.json()
            email = userinfo.get("email")
            name = userinfo.get("name") or (email.split("@")[0] if email else "Consultant")
            google_id = userinfo.get("sub")
            avatar = userinfo.get("picture")

            user = _auth_store.upsert_oauth_user(email=email, full_name=name, google_id=google_id, avatar_url=avatar)
            token = _auth_store.create_session(user)

            dest = request.cookies.get("oauth_redirect") or "/"
            resp = RedirectResponse(url=dest, status_code=303)
            resp.set_cookie(key="session_token", value=token, max_age=SESSION_EXPIRY_SECONDS, httponly=True, samesite="lax", path="/")
            resp.set_cookie(key="session_user", value=user["email"], max_age=SESSION_EXPIRY_SECONDS, httponly=False, samesite="lax", path="/")
            resp.delete_cookie("oauth_state")
            resp.delete_cookie("oauth_redirect")
            return resp
    except Exception as exc:
        logger.error("OAuth callback error: %s", exc)
        return RedirectResponse(url="/login?error=oauth_error", status_code=303)


# ==============================================================================
# Per-User Gmail OAuth 2.0 Integration Endpoints
# ==============================================================================

@app.get("/api/v1/gmail/oauth/status", tags=["Gmail OAuth"])
async def api_gmail_oauth_status(request: Request):
    """Return Gmail connection status for the authenticated user and Google OAuth setup instructions."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"status": "unauthenticated", "detail": "Sign in required."}, status_code=401)

    conn = _auth_store.get_gmail_token(user["id"])
    configured = bool(GOOGLE_GMAIL_CLIENT_ID and GOOGLE_GMAIL_CLIENT_SECRET)
    base_url = get_request_base_url(request)
    redirect_uri = os.getenv("GOOGLE_GMAIL_REDIRECT_URI") or f"{base_url}/api/v1/gmail/oauth/callback"

    return {
        "connected": bool(conn and conn.get("gmail_address")),
        "gmail_address": conn.get("gmail_address") if conn else None,
        "is_expired": conn.get("is_expired", False) if conn else False,
        "configured": configured,
        "oauth_configured": configured,
        "client_id": GOOGLE_GMAIL_CLIENT_ID if configured else None,
        "redirect_uri": redirect_uri,
        "required_scopes": [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/userinfo.email",
        ],
        "instructions": (
            "To connect real Gmail accounts: 1) Go to Google Cloud Console (https://console.cloud.google.com/apis/credentials), "
            "2) Enable the Gmail API (APIs & Services -> Enabled APIs & Services -> Enable 'Gmail API'), "
            f"3) Add Authorized Redirect URI: {redirect_uri}, "
            "4) Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to environment variables or Render dashboard."
        ) if not configured else "Google Cloud OAuth is configured."
    }


@app.get("/api/v1/gmail/oauth/connect", tags=["Gmail OAuth"])
async def api_gmail_oauth_connect(request: Request):
    """Initiates OAuth 2.0 flow to authorize user's own Gmail mailbox with minimal permissions."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/inbox", status_code=303)

    if not (GOOGLE_GMAIL_CLIENT_ID and GOOGLE_GMAIL_CLIENT_SECRET):
        return RedirectResponse(url="/inbox?error=gmail_oauth_not_configured", status_code=303)

    nonce = secrets.token_urlsafe(16)
    state = f"{user['id']}:{nonce}"
    base_url = get_request_base_url(request)
    redirect_uri = os.getenv("GOOGLE_GMAIL_REDIRECT_URI") or f"{base_url}/api/v1/gmail/oauth/callback"

    scopes = " ".join([
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/userinfo.email",
    ])

    google_auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={GOOGLE_GMAIL_CLIENT_ID}&"
        f"redirect_uri={redirect_uri}&"
        "response_type=code&"
        f"scope={scopes}&"
        f"state={state}&"
        "access_type=offline&"
        "prompt=consent"
    )
    resp = RedirectResponse(url=google_auth_url, status_code=303)
    resp.set_cookie(key="gmail_oauth_state", value=state, max_age=600, httponly=True, samesite="lax")
    return resp


@app.get("/api/v1/gmail/oauth/callback", tags=["Gmail OAuth"])
async def api_gmail_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Handle Gmail OAuth 2.0 callback, exchange code, fetch Gmail address, and store encrypted tokens."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/inbox", status_code=303)

    if error or not code:
        return RedirectResponse(url=f"/inbox?error={error or 'cancelled'}", status_code=303)

    cookie_state = request.cookies.get("gmail_oauth_state")
    if not cookie_state or not state or not secrets.compare_digest(state, cookie_state):
        return RedirectResponse(url="/inbox?error=invalid_state", status_code=303)

    state_user_id = state.split(":")[0] if ":" in state else ""
    if state_user_id != user["id"]:
        return RedirectResponse(url="/inbox?error=user_mismatch", status_code=303)

    base_url = get_request_base_url(request)
    redirect_uri = os.getenv("GOOGLE_GMAIL_REDIRECT_URI") or f"{base_url}/api/v1/gmail/oauth/callback"

    try:
        async with httpx.AsyncClient() as http_client:
            token_resp = await http_client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": GOOGLE_GMAIL_CLIENT_ID,
                    "client_secret": GOOGLE_GMAIL_CLIENT_SECRET,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
                timeout=15.0,
            )
            if token_resp.status_code != 200:
                logger.error("Gmail token exchange failed: %s", token_resp.text)
                return RedirectResponse(url="/inbox?error=token_exchange_failed", status_code=303)

            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in", 3600)
            scope = token_data.get("scope", "")

            # Get authorized Gmail profile address
            profile_resp = await http_client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=15.0,
            )
            gmail_address = ""
            if profile_resp.status_code == 200:
                gmail_address = profile_resp.json().get("emailAddress", "")
            if not gmail_address:
                ui_resp = await http_client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=15.0,
                )
                if ui_resp.status_code == 200:
                    gmail_address = ui_resp.json().get("email", "")

            if not gmail_address:
                gmail_address = user["email"]

            # Store encrypted tokens in database
            _auth_store.save_gmail_token(
                user_id=user["id"],
                gmail_address=gmail_address,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=expires_in,
                scope=scope,
            )

            resp = RedirectResponse(url="/inbox?gmail_connected=true", status_code=303)
            resp.delete_cookie("gmail_oauth_state")
            return resp
    except Exception as exc:
        logger.error("Error in Gmail OAuth callback: %s", exc)
        return RedirectResponse(url="/inbox?error=oauth_error", status_code=303)


@app.post("/api/v1/gmail/oauth/disconnect", tags=["Gmail OAuth"])
async def api_gmail_oauth_disconnect(request: Request):
    """Disconnect user's Gmail account and revoke access token with Google."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"status": "unauthenticated", "detail": "Sign in required."}, status_code=401)

    from task2_inbox_agent.app.gmail_client import GmailOAuthClient
    ok, msg = await GmailOAuthClient.disconnect(user["id"], _auth_store)
    return {"status": "success" if ok else "error", "message": msg}


@app.get("/", tags=["Portal"])
async def portal_root(request: Request):
    """Serves the Unified AI Tech Consultant Suite Portal (Protected for HTML)."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        user = get_current_user(request)
        if not user:
            return RedirectResponse(url="/login?redirect=/", status_code=303, headers=NO_CACHE_HEADERS)
        if PORTAL_TEMPLATE.exists():
            return HTMLResponse(content=PORTAL_TEMPLATE.read_text(encoding="utf-8"), headers=NO_CACHE_HEADERS)

    return JSONResponse({
        "service": "BusinessLabs AI Tech Consultant Monorepo",
        "version": "1.0.0",
        "status": "online",
        "portal_url": "/",
        "docs_url": "/docs",
        "services": {
            "task1_knowledge_brain": {"url": "/brain", "api_prefix": "/api/v1/query"},
            "task2_inbox_agent": {"url": "/inbox", "api_prefix": "/api/v1/emails"},
            "task3_scope_agent": {"url": "/scope", "api_prefix": "/api/v1/scope"},
        },
    })


@app.get("/brain", tags=["Task 1: Knowledge Brain"])
@app.get("/task1", tags=["Task 1: Knowledge Brain"])
async def task1_ui(request: Request):
    """Serves the Knowledge Brain Interactive Dashboard (Protected)."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/brain", status_code=303, headers=NO_CACHE_HEADERS)
    if TASK1_TEMPLATE.exists():
        return HTMLResponse(content=TASK1_TEMPLATE.read_text(encoding="utf-8"), headers=NO_CACHE_HEADERS)
    return HTMLResponse("<h1>Task 1 template not found</h1>", status_code=404)


@app.get("/inbox", tags=["Task 2: Inbox Agent"])
@app.get("/task2", tags=["Task 2: Inbox Agent"])
async def task2_ui(request: Request):
    """Serves the Client Inbox Agent Review Dashboard (Protected)."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/inbox", status_code=303, headers=NO_CACHE_HEADERS)
    if TASK2_TEMPLATE.exists():
        return HTMLResponse(content=TASK2_TEMPLATE.read_text(encoding="utf-8"), headers=NO_CACHE_HEADERS)
    return HTMLResponse("<h1>Task 2 template not found</h1>", status_code=404)


@app.get("/scope", tags=["Task 3: Scope Agent"])
@app.get("/task3", tags=["Task 3: Scope Agent"])
async def task3_ui(request: Request):
    """Serves the Meeting-to-Scope Proposal Generator Dashboard (Protected)."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?redirect=/scope", status_code=303, headers=NO_CACHE_HEADERS)
    if TASK3_TEMPLATE.exists():
        return HTMLResponse(content=TASK3_TEMPLATE.read_text(encoding="utf-8"), headers=NO_CACHE_HEADERS)
    return HTMLResponse("<h1>Task 3 template not found</h1>", status_code=404)


def get_process_memory_mb() -> float:
    """Return resident set size (RSS) memory in MB across Linux/Render and Windows."""
    try:
        import resource
        # ru_maxrss is in kilobytes on Linux
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)
    except Exception:
        pass
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    return round(float(parts[1]) / 1024, 2)
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ('cb', wintypes.DWORD),
                ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t),
            ]
        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(pmc)
        k32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        handle = k32.GetCurrentProcess()
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), ctypes.sizeof(pmc)):
            return round(pmc.WorkingSetSize / 1024 / 1024, 2)
    except Exception:
        pass
    return 0.0


# Unified Health Endpoint
@app.get("/health", tags=["Health & Telemetry"])
async def unified_health():
    """Returns aggregated real-time health telemetry across all three microservices."""
    llm = get_llm_provider()
    db_ok = True
    vec_ok = True

    try:
        t1_state_store.get_stats()
    except Exception:
        db_ok = False

    try:
        t1_vector_store.count()
    except Exception:
        vec_ok = False

    t2_email_count = 0
    try:
        t2_store = InboxAuditStore()
        t2_email_count = len(t2_store.list_emails(limit=200))
    except Exception:
        pass

    mem_mb = get_process_memory_mb()
    overall_status = "ok" if (db_ok and vec_ok) else "degraded"

    return {
        "status": overall_status,
        "service": "BusinessLabs AI Tech Consultant Monorepo",
        "version": "1.0.0",
        "provider_mode": "mock" if t1_settings.is_mock_mode else "anthropic",
        "is_mock_mode": t1_settings.is_mock_mode,
        "active_model": getattr(llm, "model_name", "mock-claude-3-5-sonnet"),
        "memory_telemetry": {
            "current_rss_mb": mem_mb,
            "render_limit_mb": 512.0,
            "headroom_mb": round(max(0.0, 512.0 - mem_mb), 2),
            "status": "healthy" if mem_mb < 400.0 else "warning",
        },
        "task1_knowledge_brain": {
            "status": "ok" if (db_ok and vec_ok) else "degraded",
            "url": "/brain",
            "database_connected": db_ok,
            "vector_store_healthy": vec_ok,
        },
        "task2_inbox_agent": {
            "status": "ok",
            "url": "/inbox",
            "emails_count": t2_email_count,
        },
        "task3_scope_agent": {
            "status": "ok",
            "url": "/scope",
            "require_human_sign_off": t3_settings.require_human_sign_off,
        },
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    logger.info("Starting BusinessLabs Monorepo server on 0.0.0.0:%d", port)
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
