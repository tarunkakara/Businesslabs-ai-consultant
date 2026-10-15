"""XML Sitemap Parser for Brilliant Directories Solution Center."""

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
import httpx


class SitemapParser:
    """Fetches and parses sitemap.xml files with namespace tolerance."""

    SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    def __init__(self, sitemap_url: str, timeout_seconds: float = 20.0):
        self.sitemap_url = sitemap_url
        self.timeout_seconds = timeout_seconds

    async def fetch_and_parse(self) -> List[Dict[str, Optional[str]]]:
        """Fetch remote sitemap over HTTP and parse all URL entries."""
        headers = {
            "User-Agent": "BusinessLabs-KnowledgeBrain/1.0 (+https://businesslabs.ai)"
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.get(self.sitemap_url, headers=headers)
            response.raise_for_status()
            return self.parse_xml(response.content)

    def parse_xml(self, xml_content: bytes) -> List[Dict[str, Optional[str]]]:
        """Parse raw XML content into structured URL records."""
        root = ET.fromstring(xml_content)
        entries: List[Dict[str, Optional[str]]] = []

        # Find all <url> tags (supporting both namespaced and bare XML)
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
                lastmod = lastmod_elem.text.strip() if (lastmod_elem is not None and lastmod_elem.text) else None
                entries.append({"url": url, "lastmod": lastmod})

        return entries

    @staticmethod
    def filter_article_urls(entries: List[Dict[str, Optional[str]]]) -> List[Dict[str, Optional[str]]]:
        """Filter for solution articles and knowledge base documentation."""
        return [
            entry for entry in entries
            if entry.get("url") and "/support/solutions" in entry["url"]
        ]
