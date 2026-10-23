"""REST API Routes for Task 1: Knowledge Brain."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from task1_knowledge_brain.app.crawler.runner import ResumableCrawler
from task1_knowledge_brain.app.crawler.state import CrawlStateStore
from task1_knowledge_brain.app.config import settings
from task1_knowledge_brain.app.providers.base import GroundedAnswer
from task1_knowledge_brain.app.rag.engine import KnowledgeBrainEngine
from task1_knowledge_brain.app.rag.expert_store import ExpertFeedbackStore
from task1_knowledge_brain.app.rag.store import RAGVectorStore
from task1_knowledge_brain.app.rag.sync import KnowledgeBrainSynchronizer

router = APIRouter(prefix="/api/v1", tags=["Knowledge Brain"])

# Global service instances
_state_store = CrawlStateStore(settings.sqlite_db_path)
_vector_store = RAGVectorStore(settings.chroma_persist_directory)
_expert_store = ExpertFeedbackStore(settings.sqlite_db_path)
_crawler = ResumableCrawler(state_store=_state_store)
_synchronizer = KnowledgeBrainSynchronizer(
    state_store=_state_store,
    vector_store=_vector_store,
)
_engine = KnowledgeBrainEngine(
    vector_store=_vector_store,
    expert_store=_expert_store,
)


# Request & Response Models
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=2, description="User question to answer using RAG")
    top_k: int = Field(default=5, ge=1, le=10, description="Number of context chunks to retrieve")


class CrawlBatchRequest(BaseModel):
    batch_size: int = Field(default=3, ge=1, le=10, description="Number of URLs to crawl in this batch (max 10 per batch)")


class ExpertApprovalRequest(BaseModel):
    question: str = Field(..., min_length=2)
    answer: str = Field(..., min_length=5)
    expert_name: str = Field(default="Senior Tech Consultant")
    sources: Optional[List[Dict[str, Any]]] = Field(default=None)


class SitemapInspectRequest(BaseModel):
    sitemaps: List[str] = Field(..., description="List of single or multiple sitemap URLs")
    pages_per_5_min: int = Field(default=3, ge=1, le=50, description="Estimated pages processed per 5-minute crawl cycle")


class SitemapConfirmRequest(BaseModel):
    sitemaps: List[str] = Field(..., description="Confirmed sitemap URLs")
    frequency: str = Field(default="daily", description="Update frequency: daily, weekly, every 15 days, every 30 days")
    missing_check_threshold: int = Field(default=15, description="Number of parallel rechecks before URL removal: 1 (immediate), 2, 15, or 30")
    pages_per_5_min: int = Field(default=3, ge=1, le=50)


class CrawlerSettingsUpdate(BaseModel):
    frequency: Optional[str] = None
    missing_check_threshold: Optional[str] = None
    pages_per_5_min: Optional[str] = None


# Routes
@router.post("/query", response_model=GroundedAnswer, summary="Ask a question with grounded citations")
async def ask_question(request: QueryRequest):
    """Answer question with source citations or truthful refusal when evidence is insufficient."""
    try:
        return await _engine.answer_question(question=request.question, top_k=request.top_k)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Query execution failed: {str(exc)}")


@router.post("/crawl/sync", summary="Detect added, modified, and removed pages from sitemap")
async def sync_sitemap():
    """Fetch sitemap, update SQLite state store, and purge removed articles from vector store."""
    try:
        import gc
        sync_stats = await _crawler.sync_sitemap()
        index_stats = _synchronizer.sync()
        gc.collect()
        return {
            "status": "success",
            "sitemap_changes": sync_stats,
            "vector_index_sync": index_stats,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sitemap sync failed: {str(exc)}")


@router.post("/crawl/batch", summary="Process a batch of pending articles")
async def crawl_batch(request: CrawlBatchRequest):
    """Crawl next batch of pending articles, extract content & screenshots, and index into vector store."""
    try:
        import gc
        batch_size = min(max(1, request.batch_size), 5)
        crawl_result = await _crawler.crawl_batch(batch_size=batch_size)
        crawled_urls = crawl_result.get("crawled_urls", [])
        if crawled_urls:
            index_stats = _synchronizer.sync_specific_urls(crawled_urls)
        else:
            index_stats = {"indexed_articles": 0, "total_chunks_in_index": _vector_store.count()}
        gc.collect()
        return {
            "status": "success",
            "crawl_result": crawl_result,
            "vector_index_sync": index_stats,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Batch crawl failed: {str(exc)}")


@router.get("/crawl/status", summary="Get crawl and indexing status")
async def get_crawl_status():
    """Returns total articles crawled, pending, removed, and indexed chunks count."""
    stats = _state_store.get_stats()
    stats["vector_chunks_count"] = _vector_store.count()
    stats["is_mock_mode"] = settings.is_mock_mode
    stats["active_model"] = getattr(_engine.llm_provider, "model_name", "unknown")
    stats["retrieval_status"] = "healthy"
    stats["unanswered_count"] = _expert_store.count_pending_questions()
    return stats


@router.get("/articles", summary="List crawled articles")
async def list_articles(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Paginated list of crawled articles."""
    articles = _state_store.list_articles(limit=limit, offset=offset)
    return {
        "articles": articles,
        "limit": limit,
        "offset": offset,
        "count": len(articles),
    }


@router.get("/articles/detail", summary="Get full article with extracted images")
async def get_article_detail(url: str = Query(..., description="Full URL of the article")):
    """Inspect extracted text, metadata, and associated screenshots."""
    article = _state_store.get_article(url)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


@router.get("/feedback/unanswered", summary="List unanswered questions needing human review")
async def get_unanswered_questions(limit: int = Query(default=50, ge=1, le=100)):
    """Retrieve queue of questions where evidence was insufficient."""
    return _expert_store.get_pending_questions(limit=limit)


@router.post("/feedback/approve", summary="Approve human expert answer")
async def approve_expert_answer(request: ExpertApprovalRequest):
    """Submit human expert solution, resolve question, and index into knowledge base."""
    try:
        approved = _engine.submit_expert_approval(
            question=request.question,
            answer=request.answer,
            expert_name=request.expert_name,
            sources=request.sources,
        )
        return {
            "status": "approved",
            "record": approved,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Approval failed: {str(exc)}")


@router.get("/feedback/approved", summary="List verified human expert answers")
async def list_approved_answers(limit: int = Query(default=50, ge=1, le=100)):
    """List approved expert solutions available in the knowledge base."""
    return _expert_store.list_approved_answers(limit=limit)


@router.post("/sitemap/inspect", summary="Pre-crawl inspection of single, multiple, or nested sitemaps")
async def inspect_sitemaps_endpoint(request: SitemapInspectRequest):
    """Recursively traverses nested sitemaps, reports total sitemaps, total URLs, first/last URL, and time estimate."""
    try:
        summary = await _crawler.sitemap_parser.inspect_sitemaps(
            urls=request.sitemaps,
            pages_per_5_min=request.pages_per_5_min,
        )
        return {
            "status": "success",
            "summary": summary.to_dict(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Sitemap inspection failed: {str(exc)}")


@router.post("/sitemap/confirm", summary="Confirm sitemaps, update policy, and sync database")
async def confirm_sitemaps_endpoint(request: SitemapConfirmRequest):
    """Saves frequency and missing threshold policies, syncs all detected URLs into state store."""
    try:
        import json
        import gc

        # 1. Save settings
        _state_store.set_setting("frequency", request.frequency)
        _state_store.set_setting("missing_check_threshold", str(request.missing_check_threshold))
        _state_store.set_setting("pages_per_5_min", str(request.pages_per_5_min))
        _state_store.set_setting("sitemaps_list", json.dumps(request.sitemaps))

        # 2. Inspect all sitemaps recursively
        summary = await _crawler.sitemap_parser.inspect_sitemaps(
            urls=request.sitemaps,
            pages_per_5_min=request.pages_per_5_min,
        )

        # 3. Synchronize URLs with configurable missing check re-verifications
        sync_stats = _state_store.sync_sitemap_urls(
            summary.entries,
            missing_threshold=request.missing_check_threshold,
        )

        # 4. Purge any newly removed articles from vector store
        purged_count = _synchronizer.purge_removed_articles()

        gc.collect()

        return {
            "status": "confirmed",
            "sync_stats": sync_stats,
            "purged_from_vector_store": purged_count,
            "summary": summary.to_dict(),
            "settings": {
                "frequency": request.frequency,
                "missing_check_threshold": request.missing_check_threshold,
                "pages_per_5_min": request.pages_per_5_min,
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Confirmation failed: {str(exc)}")


@router.get("/sitemap/settings", summary="Get crawler schedule and retention settings")
async def get_crawler_settings_endpoint():
    """Returns update frequency, missing check threshold, and configured sitemaps."""
    return _state_store.get_all_settings()


@router.post("/sitemap/settings", summary="Update crawler schedule and retention settings")
async def update_crawler_settings_endpoint(settings_update: CrawlerSettingsUpdate):
    """Update crawler schedule or missing URL verification threshold."""
    if settings_update.frequency:
        _state_store.set_setting("frequency", settings_update.frequency)
    if settings_update.missing_check_threshold:
        _state_store.set_setting("missing_check_threshold", settings_update.missing_check_threshold)
    if settings_update.pages_per_5_min:
        _state_store.set_setting("pages_per_5_min", settings_update.pages_per_5_min)
    return {"status": "updated", "settings": _state_store.get_all_settings()}


@router.get("/kb2/archive", summary="List removed pages archived in Knowledge Base #2")
async def get_kb2_archive_endpoint(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Returns pages that failed missing URL recheck thresholds and were archived into Database #2."""
    archived = _state_store.get_kb2_archive(limit=limit, offset=offset)
    return {
        "archived_articles": archived,
        "count": len(archived),
        "limit": limit,
        "offset": offset,
    }


@router.get("/crawler/self-healing-logs", summary="List self-healing remediation logs")
async def get_self_healing_logs_endpoint(limit: int = Query(default=50, ge=1, le=100)):
    """Returns audit log of autonomous self-healing triggered on 300/404 crashes."""
    return _state_store.get_self_healing_logs(limit=limit)
