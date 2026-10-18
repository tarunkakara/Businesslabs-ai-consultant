"""Seed loader for Task 1: Knowledge Brain.

Populates foundational crawled articles from local JSON seed file on startup
if the state database has 0 crawled articles.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from task1_knowledge_brain.app.crawler.state import CrawlStateStore
from task1_knowledge_brain.app.rag.sync import KnowledgeBrainSynchronizer

logger = logging.getLogger("knowledge_brain.crawler.seed")

SEED_FILE_PATH = Path(__file__).resolve().parent / "seed_articles.json"


def seed_core_articles(
    state_store: CrawlStateStore,
    synchronizer: Optional[KnowledgeBrainSynchronizer] = None,
) -> int:
    """Loads foundational Brilliant Directories solution articles if database has 0 crawled articles."""
    stats = state_store.get_stats()
    if stats.get("crawled", 0) > 0:
        return 0

    if not SEED_FILE_PATH.exists():
        logger.warning("Seed articles file not found at %s", SEED_FILE_PATH)
        return 0

    try:
        articles = json.loads(SEED_FILE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to parse seed articles JSON: %s", exc)
        return 0

    inserted_count = 0
    # First sync URLs into crawl_records
    sitemap_items = [{"url": a["url"], "lastmod": a.get("lastmod")} for a in articles]
    state_store.sync_sitemap_urls(sitemap_items)

    for a in articles:
        url = a["url"]
        title = a.get("title", "Article")
        breadcrumbs = a.get("breadcrumbs", "").split(" > ")
        body_text = a.get("body_text", "")
        images = json.loads(a.get("image_metadata_json", "[]")) if a.get("image_metadata_json") else []
        content_hash = a.get("content_hash", f"seed_{hash(url)}")

        state_store.mark_crawled(
            url=url,
            title=title,
            breadcrumbs=breadcrumbs,
            body_text=body_text,
            images=images,
            content_hash=content_hash,
        )
        inserted_count += 1

    logger.info("Successfully seeded %d core articles into state store", inserted_count)

    if synchronizer:
        sync_stats = synchronizer.sync()
        logger.info("Synchronized seed articles to vector store: %s", sync_stats)

    return inserted_count
