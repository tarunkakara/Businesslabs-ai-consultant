"""SQLite-backed State Store for Resumable Crawling & Change Tracking."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class CrawlStateStore:
    """Manages crawl state, change detection, and article persistence in SQLite."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize tables for crawl tracking and article storage."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS crawl_records (
                    url TEXT PRIMARY KEY,
                    lastmod TEXT,
                    etag TEXT,
                    status TEXT DEFAULT 'pending',
                    title TEXT,
                    content_hash TEXT,
                    last_crawled_at TEXT,
                    retry_count INTEGER DEFAULT 0,
                    error_message TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS article_content (
                    url TEXT PRIMARY KEY,
                    title TEXT,
                    breadcrumbs TEXT,
                    body_text TEXT,
                    image_metadata_json TEXT,
                    crawled_at TEXT,
                    FOREIGN KEY (url) REFERENCES crawl_records(url) ON DELETE CASCADE
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_crawl_status ON crawl_records(status)
            """)
            conn.commit()

    def sync_sitemap_urls(self, sitemap_items: List[Dict[str, Optional[str]]]) -> Dict[str, int]:
        """Detect added, modified, unchanged, and removed URLs."""
        counts = {"new": 0, "updated": 0, "removed": 0, "unchanged": 0}
        current_urls = {item["url"] for item in sitemap_items if item.get("url")}

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT url, lastmod, status FROM crawl_records")
            existing_records = {row["url"]: dict(row) for row in cursor.fetchall()}

            for item in sitemap_items:
                url = item["url"]
                lastmod = item.get("lastmod")

                if not url:
                    continue

                if url not in existing_records:
                    cursor.execute("""
                        INSERT INTO crawl_records (url, lastmod, status)
                        VALUES (?, ?, 'pending')
                    """, (url, lastmod))
                    counts["new"] += 1
                else:
                    existing = existing_records[url]
                    # If lastmod has changed or previous crawl failed, mark pending
                    if lastmod and existing.get("lastmod") != lastmod:
                        cursor.execute("""
                            UPDATE crawl_records
                            SET lastmod = ?, status = 'pending', retry_count = 0
                            WHERE url = ?
                        """, (lastmod, url))
                        counts["updated"] += 1
                    elif existing.get("status") == "removed":
                        cursor.execute("""
                            UPDATE crawl_records SET status = 'pending', lastmod = ? WHERE url = ?
                        """, (lastmod, url))
                        counts["updated"] += 1
                    else:
                        counts["unchanged"] += 1

            # Detect removed pages
            for existing_url, existing in existing_records.items():
                if existing_url not in current_urls and existing.get("status") != "removed":
                    cursor.execute("""
                        UPDATE crawl_records SET status = 'removed' WHERE url = ?
                    """, (existing_url,))
                    counts["removed"] += 1

            conn.commit()

        return counts

    def get_pending_urls(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch batch of pending URLs for resumable processing."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT url, lastmod, retry_count
                FROM crawl_records
                WHERE status = 'pending'
                ORDER BY retry_count ASC,
                         CASE WHEN url LIKE '%/articles/%' THEN 0 ELSE 1 END,
                         lastmod DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def mark_crawled(
        self,
        url: str,
        title: str,
        breadcrumbs: List[str],
        body_text: str,
        images: List[Dict[str, str]],
        content_hash: str,
    ) -> None:
        """Store extracted content and mark status as crawled."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE crawl_records
                SET status = 'crawled',
                    title = ?,
                    content_hash = ?,
                    last_crawled_at = ?,
                    error_message = NULL
                WHERE url = ?
            """, (title, content_hash, now, url))

            cursor.execute("""
                INSERT INTO article_content (url, title, breadcrumbs, body_text, image_metadata_json, crawled_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title = excluded.title,
                    breadcrumbs = excluded.breadcrumbs,
                    body_text = excluded.body_text,
                    image_metadata_json = excluded.image_metadata_json,
                    crawled_at = excluded.crawled_at
            """, (
                url,
                title,
                " > ".join(breadcrumbs),
                body_text,
                json.dumps(images),
                now,
            ))
            conn.commit()

    def mark_failed(self, url: str, error_message: str) -> None:
        """Record crawl failure and increment retry count."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE crawl_records
                SET status = 'failed',
                    retry_count = retry_count + 1,
                    error_message = ?
                WHERE url = ?
            """, (error_message, url))
            conn.commit()

    def get_removed_urls(self) -> List[str]:
        """Fetch all URLs marked as removed (for vector store purging)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT url FROM crawl_records WHERE status = 'removed'")
            return [row["url"] for row in cursor.fetchall()]

    def get_stats(self) -> Dict[str, int]:
        """Aggregate summary counts of crawl progress."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, COUNT(*) as count FROM crawl_records GROUP BY status")
            status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}
            cursor.execute("SELECT COUNT(*) as total FROM crawl_records")
            total = cursor.fetchone()["total"]

            return {
                "total": total,
                "crawled": status_counts.get("crawled", 0),
                "pending": status_counts.get("pending", 0),
                "failed": status_counts.get("failed", 0),
                "removed": status_counts.get("removed", 0),
            }

    def get_article(self, url: str) -> Optional[Dict[str, Any]]:
        """Retrieve full extracted article record."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT c.url, c.status, c.lastmod, c.last_crawled_at,
                       a.title, a.breadcrumbs, a.body_text, a.image_metadata_json
                FROM crawl_records c
                LEFT JOIN article_content a ON c.url = a.url
                WHERE c.url = ?
            """, (url,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["images"] = json.loads(data["image_metadata_json"]) if data.get("image_metadata_json") else []
            return data

    def list_articles(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """List crawled articles for inspection."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT c.url, c.title, c.lastmod, c.last_crawled_at, c.status
                FROM crawl_records c
                WHERE c.status = 'crawled'
                ORDER BY c.last_crawled_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            return [dict(row) for row in cursor.fetchall()]
