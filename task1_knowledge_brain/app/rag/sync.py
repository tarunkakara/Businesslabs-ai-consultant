"""Incremental Synchronization between SQLite Crawl State and ChromaDB Vector Store."""

import logging
from typing import Any, Dict, Optional

from task1_knowledge_brain.app.crawler.state import CrawlStateStore
from task1_knowledge_brain.app.rag.chunker import DocumentChunker
from task1_knowledge_brain.app.rag.store import RAGVectorStore

logger = logging.getLogger("knowledge_brain.rag.sync")


class KnowledgeBrainSynchronizer:
    """Synchronizes crawled articles into vector database and purges removed pages."""

    def __init__(
        self,
        state_store: Optional[CrawlStateStore] = None,
        vector_store: Optional[RAGVectorStore] = None,
        chunker: Optional[DocumentChunker] = None,
    ):
        self.state_store = state_store or CrawlStateStore(None)  # uses default path if None passed
        self.vector_store = vector_store or RAGVectorStore()
        self.chunker = chunker or DocumentChunker()

    def sync(self) -> Dict[str, Any]:
        """Perform full incremental synchronization."""
        # 1. Purge removed articles from vector store
        removed_urls = self.state_store.get_removed_urls()
        purged_count = 0
        for url in removed_urls:
            purged_chunks = self.vector_store.purge_article(url)
            if purged_chunks > 0:
                purged_count += 1

        # 2. Index all crawled articles
        crawled_articles = self.state_store.list_articles(limit=5000, offset=0)
        indexed_articles = 0
        total_chunks = 0

        for item in crawled_articles:
            url = item["url"]
            full_article = self.state_store.get_article(url)
            if not full_article or not full_article.get("body_text"):
                continue

            chunks = self.chunker.chunk_article(
                url=url,
                title=full_article.get("title") or "Article",
                breadcrumbs=full_article.get("breadcrumbs") or "",
                body_text=full_article["body_text"],
                images=full_article.get("images", []),
            )

            chunk_count = self.vector_store.add_article_chunks(url, chunks)
            if chunk_count > 0:
                indexed_articles += 1
                total_chunks += chunk_count

        return {
            "purged_articles": purged_count,
            "indexed_articles": indexed_articles,
            "total_chunks_in_index": self.vector_store.count(),
        }
