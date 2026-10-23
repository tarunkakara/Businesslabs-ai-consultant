"""XML Sitemap Parser supporting Multiple and Nested (Recursive) Sitemaps."""

import logging
import math
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set
import httpx

logger = logging.getLogger("knowledge_brain.crawler.sitemap")


class SitemapInspectionSummary:
    """Structured inspection summary for user confirmation."""

    def __init__(
        self,
        sitemaps_count: int,
        total_urls: int,
        first_url: Optional[str],
        last_url: Optional[str],
        estimated_time: Dict[str, Any],
        entries: List[Dict[str, Optional[str]]],
        sitemap_urls: List[str],
    ):
        self.sitemaps_count = sitemaps_count
        self.total_urls = total_urls
        self.first_url = first_url
        self.last_url = last_url
        self.estimated_time = estimated_time
        self.entries = entries
        self.sitemap_urls = sitemap_urls

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sitemaps_count": self.sitemaps_count,
            "total_urls": self.total_urls,
            "first_url": self.first_url,
            "last_url": self.last_url,
            "estimated_time": self.estimated_time,
            "sitemap_urls": self.sitemap_urls,
        }


class SitemapParser:
    """Fetches and parses sitemap.xml files with recursive nested sitemap support."""

    SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    def __init__(self, sitemap_url: Optional[str] = None, timeout_seconds: float = 20.0):
        self.sitemap_url = sitemap_url
        self.timeout_seconds = timeout_seconds

    async def fetch_and_parse(self, target_url: Optional[str] = None) -> List[Dict[str, Optional[str]]]:
        """Fetch remote sitemap over HTTP and parse all URL entries (including nested)."""
        url = target_url or self.sitemap_url
        if not url:
            return []
        inspection = await self.inspect_sitemaps([url])
        return inspection.entries

    async def inspect_sitemaps(
        self,
        urls: List[str],
        pages_per_5_min: int = 3,
        max_depth: int = 4,
    ) -> SitemapInspectionSummary:
        """Recursively traverse single or multiple sitemaps, extracting nested sitemaps and direct URLs.
        
        Returns an inspection summary with total sitemaps, total URLs, first/last URL,
        and completion time estimates for user manual confirmation.
        """
        headers = {
            "User-Agent": "BusinessLabs-KnowledgeBrain/1.0 (+https://businesslabs.ai)"
        }
        all_entries: List[Dict[str, Optional[str]]] = []
        seen_urls: Set[str] = set()
        seen_sitemaps: Set[str] = set()
        pending_sitemaps = [u.strip() for u in urls if u and u.strip()]

        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            depth = 0
            while pending_sitemaps and depth < max_depth:
                current_batch = list(pending_sitemaps)
                pending_sitemaps = []
                depth += 1

                for s_url in current_batch:
                    if s_url in seen_sitemaps:
                        continue
                    seen_sitemaps.add(s_url)

                    try:
                        response = await client.get(s_url, headers=headers)
                        if response.status_code != 200:
                            logger.warning("Sitemap fetch HTTP %d for: %s", response.status_code, s_url)
                            continue
                        
                        child_sitemaps, direct_urls = self.parse_xml_both(response.content)

                        # Queue any child sitemaps (nested sitemaps)
                        for cs in child_sitemaps:
                            if cs not in seen_sitemaps and cs not in pending_sitemaps:
                                pending_sitemaps.append(cs)

                        # Add direct page URLs
                        for item in direct_urls:
                            u = item["url"]
                            if u and u not in seen_urls:
                                seen_urls.add(u)
                                all_entries.append(item)

                    except Exception as exc:
                        logger.warning("Error fetching sitemap %s: %s", s_url, exc)

        first_url = all_entries[0]["url"] if all_entries else None
        last_url = all_entries[-1]["url"] if all_entries else None
        total_urls = len(all_entries)
        time_est = self.estimate_creation_time(total_urls, pages_per_5_min=pages_per_5_min)

        return SitemapInspectionSummary(
            sitemaps_count=len(seen_sitemaps),
            total_urls=total_urls,
            first_url=first_url,
            last_url=last_url,
            estimated_time=time_est,
            entries=all_entries,
            sitemap_urls=list(seen_sitemaps),
        )

    def parse_xml_both(self, xml_content: bytes) -> tuple[List[str], List[Dict[str, Optional[str]]]]:
        """Parse raw XML content, discovering both nested sitemaps and direct URL elements."""
        try:
            root = ET.fromstring(xml_content)
        except Exception as exc:
            logger.warning("Failed to parse XML: %s", exc)
            return [], []

        child_sitemaps: List[str] = []
        direct_urls: List[Dict[str, Optional[str]]] = []
        seen_urls: Set[str] = set()

        # 1. Check for nested sitemaps (<sitemapindex> / <sitemap>)
        sitemap_elements = root.findall("sm:sitemap", self.SITEMAP_NS)
        if not sitemap_elements:
            sitemap_elements = root.findall("sitemap")

        for sm in sitemap_elements:
            loc = sm.find("sm:loc", self.SITEMAP_NS)
            if loc is None:
                loc = sm.find("loc")
            if loc is not None and loc.text:
                c_url = loc.text.strip()
                if c_url:
                    child_sitemaps.append(c_url)

        # 2. Check for direct URLs (<urlset> / <url>)
        url_elements = root.findall("sm:url", self.SITEMAP_NS)
        if not url_elements:
            url_elements = root.findall("url")

        for elem in url_elements:
            loc_elem = elem.find("sm:loc", self.SITEMAP_NS)
            if loc_elem is None:
                loc_elem = elem.find("loc")

            lastmod_elem = elem.find("sm:lastmod", self.SITEMAP_NS)
            if lastmod_elem is None:
                lastmod_elem = elem.find("lastmod")

            if loc_elem is not None and loc_elem.text:
                url = loc_elem.text.strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                lastmod = lastmod_elem.text.strip() if (lastmod_elem is not None and lastmod_elem.text) else None
                direct_urls.append({"url": url, "lastmod": lastmod})

        return child_sitemaps, direct_urls

    def parse_xml(self, xml_content: bytes) -> List[Dict[str, Optional[str]]]:
        """Backward-compatible helper to parse raw XML directly into URL records."""
        _, direct_urls = self.parse_xml_both(xml_content)
        return direct_urls

    @staticmethod
    def estimate_creation_time(total_urls: int, pages_per_5_min: int = 3) -> Dict[str, Any]:
        """Calculates estimated knowledge base creation time based on crawl batch rates."""
        if total_urls <= 0:
            return {
                "pages_per_5_min": pages_per_5_min,
                "pages_per_hour": pages_per_5_min * 12,
                "total_minutes": 0,
                "formatted": "0 minutes",
                "days_and_hours": "0 hours",
                "estimate_range": "0 days ± 1 day",
            }

        safe_rate = max(1, pages_per_5_min)
        total_batches = math.ceil(total_urls / safe_rate)
        total_minutes = total_batches * 5
        total_hours = total_minutes / 60.0
        total_days = total_hours / 24.0

        hours_int = int(total_hours)
        mins_remaining = int(total_minutes % 60)
        days_int = int(total_days)
        hours_remaining = int(total_hours % 24)

        if total_hours < 1:
            formatted = f"{total_minutes} minutes"
            days_hours_str = f"{total_minutes} minutes"
        elif total_hours < 24:
            formatted = f"{hours_int} hours {mins_remaining} mins"
            days_hours_str = f"{hours_int} hours"
        else:
            formatted = f"{days_int} days {hours_remaining} hours"
            days_hours_str = f"{days_int} days {hours_remaining} hours"

        # Plus or minus 1 day note as requested in prompt & PDF
        range_str = f"{days_hours_str} (± 1 day)" if total_hours >= 12 else f"{formatted} (± a few hours)"

        return {
            "pages_per_5_min": safe_rate,
            "pages_per_hour": safe_rate * 12,
            "total_minutes": total_minutes,
            "total_hours": round(total_hours, 1),
            "total_days": round(total_days, 1),
            "formatted": formatted,
            "days_and_hours": days_hours_str,
            "estimate_range": range_str,
        }

    @staticmethod
    def filter_article_urls(entries: List[Dict[str, Optional[str]]]) -> List[Dict[str, Optional[str]]]:
        """Filter for solution articles and knowledge base documentation."""
        return [
            entry for entry in entries
            if entry.get("url") and "/support/solutions" in entry["url"]
        ]
