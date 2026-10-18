"""Task 1: Python Knowledge Brain - FastAPI Service Entrypoint.

Provides the foundational REST API for crawling, document indexing,
grounded RAG query execution, and human expert feedback.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from task1_knowledge_brain.app.api.routes import router as api_router
from task1_knowledge_brain.app.config import settings
from task1_knowledge_brain.app.providers import get_llm_provider


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle startup and teardown handler."""
    Path(settings.chroma_persist_directory).mkdir(parents=True, exist_ok=True)
    Path(settings.sqlite_db_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        from task1_knowledge_brain.app.api.routes import _state_store, _synchronizer
        from task1_knowledge_brain.app.crawler.seed import seed_core_articles
        seed_core_articles(state_store=_state_store, synchronizer=_synchronizer)
    except Exception as exc:
        import logging
        logging.getLogger("main").warning("Startup seed check warning: %s", exc)
    yield


app = FastAPI(
    title="BusinessLabs AI Tech Consultant — Knowledge Brain",
    description=(
        "Production-grade Knowledge Brain RAG service for Brilliant Directories solution center. "
        "Provides grounded Q&A, citation verification, incremental crawl sync, and expert feedback."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for local cross-service communication (Tasks 2 & 3) and browser UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routes
app.include_router(api_router)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    provider_mode: str
    is_mock_mode: bool
    active_model: str
    database_connected: bool = True
    vector_store_healthy: bool = True


TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "index.html"


@app.get("/", tags=["UI & General"])
async def root(request: Request):
    """Serves the Knowledge Brain Web Dashboard or JSON metadata depending on Accept header."""
    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header and TEMPLATE_PATH.exists():
        return HTMLResponse(content=TEMPLATE_PATH.read_text(encoding="utf-8"))

    llm = get_llm_provider()
    return JSONResponse({
        "service": "BusinessLabs Knowledge Brain",
        "version": "1.0.0",
        "status": "online",
        "ui_url": "/",
        "docs_url": "/docs",
        "provider_mode": "mock" if settings.is_mock_mode else "anthropic",
        "active_model": getattr(llm, "model_name", "unknown"),
        "sitemap_target": settings.crawl_sitemap_url,
    })


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Health check endpoint verifying model, database, and retrieval vector store."""
    from task1_knowledge_brain.app.api.routes import _state_store, _vector_store
    llm = get_llm_provider()
    db_ok = True
    vec_ok = True
    try:
        _state_store.get_stats()
    except Exception:
        db_ok = False
    try:
        _vector_store.count()
    except Exception:
        vec_ok = False

    status_str = "ok" if (db_ok and vec_ok) else "degraded"
    return HealthResponse(
        status=status_str,
        service="task1_knowledge_brain",
        version="1.0.0",
        provider_mode="mock" if settings.is_mock_mode else "anthropic",
        is_mock_mode=settings.is_mock_mode,
        active_model=getattr(llm, "model_name", "mock-claude"),
        database_connected=db_ok,
        vector_store_healthy=vec_ok,
    )
