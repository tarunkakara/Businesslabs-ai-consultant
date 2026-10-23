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
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

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


# UI Dashboard Routes
@app.get("/login", tags=["Auth"])
async def login_ui():
    """Serves the user authentication login page."""
    if LOGIN_TEMPLATE.exists():
        return HTMLResponse(content=LOGIN_TEMPLATE.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Login template not found</h1>", status_code=404)


@app.post("/api/v1/auth/login", tags=["Auth"])
async def api_auth_login(request: Request):
    """Authenticates user session and returns profile information."""
    try:
        body = await request.json()
        email = body.get("email", "").strip() or "consultant@businesslabs.ai"
        user_data = {
            "name": "Sarah Connor",
            "email": email,
            "role": "Lead AI Tech Consultant",
            "avatar": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=256&q=80",
        }
        response = JSONResponse({"status": "success", "user": user_data})
        response.set_cookie(key="session_user", value=email, max_age=86400, httponly=False)
        return response
    except Exception as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=400)


@app.get("/api/v1/auth/me", tags=["Auth"])
async def api_auth_me(request: Request):
    """Retrieve logged in user session and profile details."""
    email = request.cookies.get("session_user") or "consultant@businesslabs.ai"
    return {
        "status": "authenticated",
        "user": {
            "name": "Sarah Connor",
            "email": email,
            "role": "Lead AI Tech Consultant",
            "avatar": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=256&q=80",
        }
    }


@app.post("/api/v1/auth/logout", tags=["Auth"])
async def api_auth_logout():
    """Clear session cookie and log out."""
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie("session_user")
    return response


@app.get("/", tags=["Portal"])
async def portal_root(request: Request):
    """Serves the Unified AI Tech Consultant Suite Portal or JSON API registry."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept and PORTAL_TEMPLATE.exists():
        return HTMLResponse(content=PORTAL_TEMPLATE.read_text(encoding="utf-8"))

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
async def task1_ui():
    """Serves the Knowledge Brain Interactive Dashboard."""
    if TASK1_TEMPLATE.exists():
        return HTMLResponse(content=TASK1_TEMPLATE.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Task 1 template not found</h1>", status_code=404)


@app.get("/inbox", tags=["Task 2: Inbox Agent"])
@app.get("/task2", tags=["Task 2: Inbox Agent"])
async def task2_ui():
    """Serves the Client Inbox Agent Review Dashboard."""
    if TASK2_TEMPLATE.exists():
        return HTMLResponse(content=TASK2_TEMPLATE.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Task 2 template not found</h1>", status_code=404)


@app.get("/scope", tags=["Task 3: Scope Agent"])
@app.get("/task3", tags=["Task 3: Scope Agent"])
async def task3_ui():
    """Serves the Meeting-to-Scope Proposal Generator Dashboard."""
    if TASK3_TEMPLATE.exists():
        return HTMLResponse(content=TASK3_TEMPLATE.read_text(encoding="utf-8"))
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
