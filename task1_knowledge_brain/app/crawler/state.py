"""SQLite-backed State Store for Resumable Crawling & Change Tracking."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class CrawlStateStore:
    """Manages crawl state, change detection, and article persistence in SQLite."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize tables for crawl tracking, KB2 archive, settings, and self-healing logs."""
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
                    error_message TEXT,
                    missing_checks_count INTEGER DEFAULT 0
                )
            """)
            # Migration check: Ensure missing_checks_count column exists
            try:
                cursor.execute("ALTER TABLE crawl_records ADD COLUMN missing_checks_count INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass

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
                CREATE TABLE IF NOT EXISTS kb2_removed_archive (
                    url TEXT PRIMARY KEY,
                    title TEXT,
                    breadcrumbs TEXT,
                    body_text TEXT,
                    image_metadata_json TEXT,
                    removed_at TEXT,
                    missing_checks_count INTEGER DEFAULT 0,
                    last_status TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS crawler_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS self_healing_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT,
                    error_code INTEGER,
                    resolved_url TEXT,
                    resolution_notes TEXT,
                    created_at TEXT
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_crawl_status ON crawl_records(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_kb2_removed ON kb2_removed_archive(removed_at)
            """)

            # Populate default settings if not already present
            defaults = {
                "frequency": "daily",
                "missing_check_threshold": "1",
                "pages_per_5_min": "3",
                "sitemaps_list": "[]",
            }
            for k, v in defaults.items():
                cursor.execute("INSERT OR IGNORE INTO crawler_settings (key, value) VALUES (?, ?)", (k, v))

            conn.commit()

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve a crawler configuration setting."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM crawler_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Save a crawler configuration setting."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO crawler_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, str(value)))
            conn.commit()

    def get_all_settings(self) -> Dict[str, str]:
        """Fetch all configuration settings."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM crawler_settings")
            return {row["key"]: row["value"] for row in cursor.fetchall()}

    def record_self_healing(
        self,
        url: str,
        error_code: int,
        resolved_url: Optional[str],
        resolution_notes: str,
    ) -> None:
        """Log self-healing remediation triggered on 300/404 crashes."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO self_healing_logs (url, error_code, resolved_url, resolution_notes, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (url, error_code, resolved_url, resolution_notes, now))
            conn.commit()

    def get_self_healing_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch recent self-healing actions."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, url, error_code, resolved_url, resolution_notes, created_at
                FROM self_healing_logs
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_kb2_archive(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Fetch removed pages archived in Knowledge Base #2."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT url, title, breadcrumbs, body_text, image_metadata_json, removed_at, missing_checks_count, last_status
                FROM kb2_removed_archive
                ORDER BY removed_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            return [dict(row) for row in cursor.fetchall()]

    def sync_sitemap_urls(
        self,
        sitemap_items: List[Dict[str, Optional[str]]],
        missing_threshold: Optional[int] = None,
    ) -> Dict[str, int]:
        """Detect added, modified, unchanged, and removed URLs with configurable missing check re-verifications."""
        counts = {"new": 0, "updated": 0, "removed": 0, "unchanged": 0, "missing_pending": 0}
        current_urls = {item["url"] for item in sitemap_items if item.get("url")}

        if missing_threshold is None:
            raw_threshold = self.get_setting("missing_check_threshold", "1")
            try:
                missing_threshold = int(raw_threshold)
            except (ValueError, TypeError):
                missing_threshold = 1

        # 'immediate' implies threshold = 1
        safe_threshold = max(1, missing_threshold)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT url, lastmod, status, missing_checks_count, title FROM crawl_records")
            existing_records = {row["url"]: dict(row) for row in cursor.fetchall()}

            for item in sitemap_items:
                url = item["url"]
                lastmod = item.get("lastmod")

                if not url:
                    continue

                if url not in existing_records:
                    cursor.execute("""
                        INSERT INTO crawl_records (url, lastmod, status, missing_checks_count)
                        VALUES (?, ?, 'pending', 0)
                    """, (url, lastmod))
                    counts["new"] += 1
                else:
                    existing = existing_records[url]
                    # Reset missing counter if it had previously gone missing
                    # If lastmod has changed or previous crawl failed/removed, mark pending
                    if lastmod and existing.get("lastmod") != lastmod:
                        cursor.execute("""
                            UPDATE crawl_records
                            SET lastmod = ?, status = 'pending', retry_count = 0, missing_checks_count = 0
                            WHERE url = ?
                        """, (lastmod, url))
                        counts["updated"] += 1
                    elif existing.get("status") == "removed":
                        cursor.execute("""
                            UPDATE crawl_records
                            SET status = 'pending', lastmod = ?, missing_checks_count = 0
                            WHERE url = ?
                        """, (lastmod, url))
                        counts["updated"] += 1
                    else:
                        cursor.execute("""
                            UPDATE crawl_records SET missing_checks_count = 0 WHERE url = ?
                        """, (url,))
                        counts["unchanged"] += 1

            # Detect and recheck missing pages
            now_iso = datetime.now(timezone.utc).isoformat()
            for existing_url, existing in existing_records.items():
                if existing_url not in current_urls and existing.get("status") != "removed":
                    current_missing = (existing.get("missing_checks_count") or 0) + 1
                    if current_missing >= safe_threshold:
                        # Rechecked N times and missing each time -> Confirm removal and archive to KB2
                        cursor.execute("""
                            UPDATE crawl_records
                            SET status = 'removed', missing_checks_count = ?
                            WHERE url = ?
                        """, (current_missing, existing_url))

                        # Fetch article content if available to archive into KB2
                        cursor.execute("""
                            SELECT title, breadcrumbs, body_text, image_metadata_json
                            FROM article_content
                            WHERE url = ?
                        """, (existing_url,))
                        art = cursor.fetchone()

                        cursor.execute("""
                            INSERT INTO kb2_removed_archive (url, title, breadcrumbs, body_text, image_metadata_json, removed_at, missing_checks_count, last_status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(url) DO UPDATE SET
                                removed_at = excluded.removed_at,
                                missing_checks_count = excluded.missing_checks_count,
                                last_status = excluded.last_status
                        """, (
                            existing_url,
                            art["title"] if art else existing.get("title"),
                            art["breadcrumbs"] if art else None,
                            art["body_text"] if art else None,
                            art["image_metadata_json"] if art else None,
                            now_iso,
                            current_missing,
                            existing.get("status"),
                        ))
                        counts["removed"] += 1
                    else:
                        # Grace period: recheck count incremented, page preserved in KB1
                        cursor.execute("""
                            UPDATE crawl_records
                            SET missing_checks_count = ?
                            WHERE url = ?
                        """, (current_missing, existing_url))
                        counts["missing_pending"] += 1

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
            cursor.execute("SELECT COUNT(*) as kb2_count FROM kb2_removed_archive")
            kb2_total = cursor.fetchone()["kb2_count"]

            return {
                "total": total,
                "crawled": status_counts.get("crawled", 0),
                "pending": status_counts.get("pending", 0),
                "failed": status_counts.get("failed", 0),
                "removed": status_counts.get("removed", 0),
                "kb2_archived": kb2_total,
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
