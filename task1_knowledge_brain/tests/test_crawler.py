"""Unit and integration tests for Sitemap Parser, State Store, and Article Extractor."""

import pytest
import httpx
from task1_knowledge_brain.app.crawler.extractor import ArticleExtractor
from task1_knowledge_brain.app.crawler.runner import ResumableCrawler
from task1_knowledge_brain.app.crawler.sitemap import SitemapParser
from task1_knowledge_brain.app.crawler.state import CrawlStateStore


SAMPLE_SITEMAP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://support.brilliantdirectories.com/support/home</loc>
    <lastmod>2026-10-01T10:00:00Z</lastmod>
  </url>
  <url>
    <loc>https://support.brilliantdirectories.com/support/solutions/articles/12000000001</loc>
    <lastmod>2026-10-05T12:00:00Z</lastmod>
  </url>
  <url>
    <loc>https://support.brilliantdirectories.com/support/solutions/articles/12000000002</loc>
    <lastmod>2026-10-10T08:30:00Z</lastmod>
  </url>
</urlset>
"""

SAMPLE_ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>How to Set Up Webhooks : Brilliant Directories</title>
</head>
<body>
    <header><nav>Header Navigation</nav></header>
    <nav aria-label="breadcrumb">
        <ul>
            <li><a href="/support/home">Home</a></li>
            <li><a href="/support/solutions">Solutions</a></li>
            <li><span>Developer Tools</span></li>
        </ul>
    </nav>
    <main>
        <div class="article-body">
            <h1>How to Set Up Webhooks</h1>
            <p>Webhooks allow Brilliant Directories to send real-time event notifications to external webhooks endpoints.</p>
            <figure>
                <img src="/images/webhook-settings.png" alt="Webhook Settings Panel" />
                <figcaption>Figure 1: The Webhook Settings configuration modal.</figcaption>
            </figure>
            <p>Navigate to Developer Tools &gt; Webhooks, click New Webhook, and input your target endpoint URL.</p>
        </div>
    </main>
    <footer><p>Copyright 2026 Brilliant Directories</p></footer>
    <script>console.log("analytics");</script>
</body>
</html>
"""


def test_sitemap_parser():
    """Verify parsing XML with namespaces and filtering solution articles."""
    parser = SitemapParser("https://mock-domain.com/sitemap.xml")
    entries = parser.parse_xml(SAMPLE_SITEMAP_XML)
    assert len(entries) == 3
    assert entries[0]["url"] == "https://support.brilliantdirectories.com/support/home"
    assert entries[1]["lastmod"] == "2026-10-05T12:00:00Z"

    filtered = parser.filter_article_urls(entries)
    assert len(filtered) == 2
    assert all("/support/solutions" in e["url"] for e in filtered)


def test_sitemap_parser_deduplication():
    """Verify that duplicate URLs in XML sitemaps are deduplicated."""
    xml_with_duplicates = b"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://support.brilliantdirectories.com/support/solutions/articles/123</loc></url>
      <url><loc>https://support.brilliantdirectories.com/support/solutions/articles/123</loc></url>
      <url><loc>https://support.brilliantdirectories.com/support/solutions/articles/456</loc></url>
    </urlset>
    """
    parser = SitemapParser("https://mock-domain.com/sitemap.xml")
    entries = parser.parse_xml(xml_with_duplicates)
    assert len(entries) == 2
    assert [e["url"] for e in entries] == [
        "https://support.brilliantdirectories.com/support/solutions/articles/123",
        "https://support.brilliantdirectories.com/support/solutions/articles/456",
    ]


def test_crawler_state_store_prioritizes_articles(tmp_path):
    """Verify that get_pending_urls prioritizes /articles/ over section directory listings."""
    db_file = str(tmp_path / "test_crawl_priority.db")
    store = CrawlStateStore(db_file)

    # Sync directory section URLs first, then solution article URLs
    items = [
        {"url": "https://support.brilliantdirectories.com/support/solutions/12000001", "lastmod": "2026-10-01"},
        {"url": "https://support.brilliantdirectories.com/support/solutions/12000002", "lastmod": "2026-10-01"},
        {"url": "https://support.brilliantdirectories.com/support/solutions/articles/12000006843-supported-payment-gateways", "lastmod": "2026-10-01"},
    ]
    store.sync_sitemap_urls(items)

    pending = store.get_pending_urls(limit=10)
    assert len(pending) == 3
    # The article URL must come FIRST
    assert "/articles/" in pending[0]["url"]
    assert "12000006843-supported-payment-gateways" in pending[0]["url"]


def test_crawler_state_store_sync_and_change_detection(tmp_path):
    """Verify new, modified, and removed page detection in SQLite."""
    db_file = str(tmp_path / "test_crawl_state.db")
    store = CrawlStateStore(db_file)

    initial_items = [
        {"url": "https://example.com/article-1", "lastmod": "2026-10-01"},
        {"url": "https://example.com/article-2", "lastmod": "2026-10-01"},
    ]
    # 1. Initial sync -> 2 new items
    counts1 = store.sync_sitemap_urls(initial_items)
    assert counts1["new"] == 2
    assert counts1["updated"] == 0
    assert counts1["removed"] == 0

    stats = store.get_stats()
    assert stats["total"] == 2
    assert stats["pending"] == 2

    # 2. Second sync with article-1 modified, article-2 removed, article-3 added
    updated_items = [
        {"url": "https://example.com/article-1", "lastmod": "2026-10-15"},  # modified
        {"url": "https://example.com/article-3", "lastmod": "2026-10-15"},  # new
    ]
    counts2 = store.sync_sitemap_urls(updated_items)
    assert counts2["new"] == 1
    assert counts2["updated"] == 1
    assert counts2["removed"] == 1

    removed_urls = store.get_removed_urls()
    assert "https://example.com/article-2" in removed_urls


def test_crawler_state_mark_crawled_and_retrieval(tmp_path):
    """Verify marking crawled and inspecting article records."""
    db_file = str(tmp_path / "test_crawl_state.db")
    store = CrawlStateStore(db_file)

    store.sync_sitemap_urls([{"url": "https://example.com/test", "lastmod": "2026-10-01"}])
    pending = store.get_pending_urls(limit=10)
    assert len(pending) == 1
    assert pending[0]["url"] == "https://example.com/test"

    store.mark_crawled(
        url="https://example.com/test",
        title="Test Article",
        breadcrumbs=["Home", "Solutions"],
        body_text="This is test documentation body text.",
        images=[{"url": "https://example.com/img.png", "alt": "Test Image"}],
        content_hash="abc123hash",
    )

    stats = store.get_stats()
    assert stats["crawled"] == 1
    assert stats["pending"] == 0

    article = store.get_article("https://example.com/test")
    assert article is not None
    assert article["title"] == "Test Article"
    assert "Home > Solutions" in article["breadcrumbs"]
    assert len(article["images"]) == 1
    assert article["images"][0]["alt"] == "Test Image"


def test_article_extractor_html():
    """Verify HTML extraction of title, breadcrumbs, clean text, and images."""
    extractor = ArticleExtractor()
    extracted = extractor.extract(
        SAMPLE_ARTICLE_HTML,
        url="https://support.brilliantdirectories.com/support/solutions/articles/12000000001",
    )

    assert extracted.title == "How to Set Up Webhooks"
    assert "Home" in extracted.breadcrumbs
    assert "Developer Tools" in extracted.breadcrumbs
    assert "real-time event notifications" in extracted.body_text
    assert "Header Navigation" not in extracted.body_text  # Nav stripped
    assert "Copyright" not in extracted.body_text          # Footer stripped
    assert "console.log" not in extracted.body_text        # Script stripped

    assert len(extracted.images) == 1
    img = extracted.images[0]
    assert img["url"] == "https://support.brilliantdirectories.com/images/webhook-settings.png"
    assert img["alt"] == "Webhook Settings Panel"
    assert "Figure 1: The Webhook Settings configuration modal." in img["caption"]
    assert len(extracted.content_hash) == 64  # SHA256 length


@pytest.mark.asyncio
async def test_resumable_crawler_mock_crawl(tmp_path):
    """Verify crawler runner crawl_single_url with mock transport."""
    db_file = str(tmp_path / "test_crawl_state.db")
    store = CrawlStateStore(db_file)
    store.sync_sitemap_urls([{"url": "https://mock.brilliantdirectories.com/article", "lastmod": "2026-10-01"}])

    crawler = ResumableCrawler(state_store=store, delay_seconds=0.0)

    # Mock HTTP response handler
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_ARTICLE_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
        success = await crawler.crawl_single_url(mock_client, "https://mock.brilliantdirectories.com/article")
        assert success is True

    stats = store.get_stats()
    assert stats["crawled"] == 1
    assert stats["pending"] == 0
