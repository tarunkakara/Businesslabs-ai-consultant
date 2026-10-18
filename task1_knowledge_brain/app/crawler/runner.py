"""Resumable, Rate-limited Crawler Runner for Brilliant Directories."""

import asyncio
import logging
from typing import Any, Dict, List, Optional
import httpx

from task1_knowledge_brain.app.config import settings
from task1_knowledge_brain.app.crawler.extractor import ArticleExtractor
from task1_knowledge_brain.app.crawler.sitemap import SitemapParser
from task1_knowledge_brain.app.crawler.state import CrawlStateStore

logger = logging.getLogger("knowledge_brain.crawler")


class ResumableCrawler:
    """Manages batching, rate limiting, retries, and resumable article crawling."""

    def __init__(
        self,
        state_store: Optional[CrawlStateStore] = None,
        sitemap_url: Optional[str] = None,
        delay_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        self.state_store = state_store or CrawlStateStore(settings.sqlite_db_path)
        self.sitemap_parser = SitemapParser(sitemap_url or settings.crawl_sitemap_url)
        self.extractor = ArticleExtractor()
        self.delay_seconds = delay_seconds if delay_seconds is not None else settings.crawl_delay_seconds
        self.max_retries = max_retries if max_retries is not None else settings.crawl_max_retries
        self._is_running = False

    async def sync_sitemap(self) -> Dict[str, int]:
        """Fetch remote sitemap and synchronize change records in SQLite."""
        logger.info("Fetching sitemap from: %s", self.sitemap_parser.sitemap_url)
        raw_items = await self.sitemap_parser.fetch_and_parse()
        article_items = self.sitemap_parser.filter_article_urls(raw_items)
        if not article_items:
            # If filter yields empty (e.g. testing different domains), use all items
            article_items = raw_items

        counts = self.state_store.sync_sitemap_urls(article_items)
        logger.info("Sitemap sync complete: %s", counts)
        return counts

    async def crawl_single_url(self, client: httpx.AsyncClient, url: str) -> bool:
        """Crawl a single URL with exponential backoff retries."""
        headers = {
            "User-Agent": "BusinessLabs-KnowledgeBrain/1.0 (+https://businesslabs.ai)"
        }
        attempt = 0
        backoff = 0.5
        max_retries = min(self.max_retries, 1)

        while attempt <= max_retries:
            try:
                response = await client.get(url, headers=headers, follow_redirects=True)
                if response.status_code == 200:
                    html_content = response.text
                    del response
                    article = self.extractor.extract(html_content, url)
                    del html_content
                    self.state_store.mark_crawled(
                        url=article.url,
                        title=article.title,
                        breadcrumbs=article.breadcrumbs,
                        body_text=article.body_text,
                        images=article.images,
                        content_hash=article.content_hash,
                    )
                    del article
                    logger.debug("Successfully crawled: %s", url)
                    return True
                elif response.status_code in [404, 403, 410]:
                    self.state_store.mark_failed(url, f"HTTP {response.status_code}")
                    return False
                else:
                    attempt += 1
                    if attempt > max_retries:
                        self.state_store.mark_failed(url, f"HTTP {response.status_code}")
                        return False
            except Exception as exc:
                attempt += 1
                if attempt > max_retries:
                    self.state_store.mark_failed(url, f"Exception: {str(exc)}")
                    return False

            await asyncio.sleep(backoff)
            backoff *= 1.5

        return False

    async def crawl_batch(self, batch_size: int = 3) -> Dict[str, Any]:
        """Crawl next pending batch of URLs concurrently respecting safe rate limits."""
        safe_batch = min(max(1, batch_size), 5)
        pending = self.state_store.get_pending_urls(limit=safe_batch)
        if not pending:
            return {"processed": 0, "successful": 0, "crawled_urls": [], "remaining_pending": 0}

        successful = 0
        crawled_urls: List[str] = []
        sem = asyncio.Semaphore(2)

        async def fetch_task(item: Dict[str, Any], client: httpx.AsyncClient):
            nonlocal successful
            url = item["url"]
            async with sem:
                ok = await self.crawl_single_url(client, url)
                if ok:
                    successful += 1
                    crawled_urls.append(url)

        async with httpx.AsyncClient(timeout=10.0) as client:
            await asyncio.gather(*(fetch_task(item, client) for item in pending))

        import gc
        gc.collect()

        stats = self.state_store.get_stats()
        return {
            "processed": len(pending),
            "successful": successful,
            "crawled_urls": crawled_urls,
            "remaining_pending": stats.get("pending", 0),
        }

    async def run_crawl_loop(self, max_articles: Optional[int] = None) -> Dict[str, Any]:
        """Continuous resumable crawl loop until pending queue is empty or limit reached."""
        self._is_running = True
        total_crawled = 0

        try:
            while self._is_running:
                limit = 10
                if max_articles is not None:
                    remaining = max_articles - total_crawled
                    if remaining <= 0:
                        break
                    limit = min(limit, remaining)

                batch_res = await self.crawl_batch(batch_size=limit)
                processed = batch_res["processed"]
                total_crawled += batch_res["successful"]

                if processed == 0:
                    break
        finally:
            self._is_running = False

        stats = self.state_store.get_stats()
        return {
            "total_crawled_in_session": total_crawled,
            "overall_stats": stats,
        }

    def stop(self) -> None:
        """Signal the crawler loop to pause after current item."""
        self._is_running = False
