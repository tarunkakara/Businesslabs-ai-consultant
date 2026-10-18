"""Article & Image Extractor using BeautifulSoup."""

import hashlib
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup


class ExtractedArticle:
    """Data container for an extracted article and associated media."""

    def __init__(
        self,
        url: str,
        title: str,
        breadcrumbs: List[str],
        body_text: str,
        images: List[Dict[str, Any]],
        content_hash: str,
    ):
        self.url = url
        self.title = title
        self.breadcrumbs = breadcrumbs
        self.body_text = body_text
        self.images = images
        self.content_hash = content_hash

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "breadcrumbs": self.breadcrumbs,
            "body_text": self.body_text,
            "images": self.images,
            "content_hash": self.content_hash,
        }


class ArticleExtractor:
    """Parses raw HTML from Brilliant Directories Solution Center into clean structured data."""

    UNWANTED_TAGS = ["script", "style", "nav", "footer", "header", "noscript", "iframe", "svg"]
    ICON_PATTERNS = ["icon", "logo", "badge", "avatar", "1x1", "spacer"]

    def extract(self, html_content: str, url: str) -> ExtractedArticle:
        soup = BeautifulSoup(html_content, "html.parser")
        try:
            # 1. Extract Title
            title = self._extract_title(soup, url)

            # 2. Extract Breadcrumbs
            breadcrumbs = self._extract_breadcrumbs(soup)

            # 3. Locate Main Article Container
            main_container = self._find_article_container(soup)

            # 4. Extract Images & Screenshots associated with this article
            images = self._extract_images(main_container, url)

            # 5. Extract Clean Text
            body_text = self._extract_clean_text(main_container)

            # 6. Compute SHA-256 Content Hash
            content_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

            return ExtractedArticle(
                url=url,
                title=title,
                breadcrumbs=breadcrumbs,
                body_text=body_text,
                images=images,
                content_hash=content_hash,
            )
        finally:
            soup.decompose()

    def _extract_title(self, soup: BeautifulSoup, url: str) -> str:
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(strip=True)

        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            clean = title_tag.get_text(strip=True)
            # Remove site suffix e.g. " : Brilliant Directories"
            return clean.split(":")[0].split("|")[0].split(" - ")[0].strip()

        # Fallback to last segment of URL
        slug = url.rstrip("/").split("/")[-1]
        return slug.replace("-", " ").capitalize()

    def _extract_breadcrumbs(self, soup: BeautifulSoup) -> List[str]:
        crumbs = []
        bc_container = (
            soup.find("nav", {"aria-label": "breadcrumb"})
            or soup.find("ul", class_=re.compile(r"breadcrumb", re.I))
            or soup.find("div", class_=re.compile(r"breadcrumb", re.I))
        )
        if bc_container:
            for item in bc_container.find_all(["li", "a", "span"]):
                text = item.get_text(strip=True)
                if text and text not in crumbs and text not in [">", "/", "»"]:
                    crumbs.append(text)
        return crumbs

    def _find_article_container(self, soup: BeautifulSoup) -> BeautifulSoup:
        # Try finding common article wrappers
        for selector in [
            {"class_": re.compile(r"(article-body|solution-body|article_body|content-body)", re.I)},
            {"class_": re.compile(r"(article|main-content|solution-content)", re.I)},
            {"name": "article"},
            {"name": "main"},
        ]:
            if "name" in selector:
                container = soup.find(selector["name"])
            else:
                container = soup.find(attrs=selector)
            if container:
                return container
        return soup.body if soup.body else soup

    def _extract_images(self, container: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
        images = []
        for img in container.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if not src:
                continue

            abs_url = urljoin(base_url, src)
            alt = (img.get("alt") or "").strip()

            # Skip tiny icons or spacers
            if any(p in abs_url.lower() for p in self.ICON_PATTERNS) and not alt:
                continue

            # Look for closest caption or parent paragraph
            caption = ""
            parent_figure = img.find_parent("figure")
            if parent_figure:
                fig_cap = parent_figure.find("figcaption")
                if fig_cap:
                    caption = fig_cap.get_text(strip=True)

            images.append({
                "url": abs_url,
                "alt": alt,
                "caption": caption,
            })
        return images

    def _extract_clean_text(self, container: BeautifulSoup) -> str:
        # Clone container to avoid mutating original
        copy_soup = BeautifulSoup(str(container), "html.parser")
        for tag in copy_soup.find_all(self.UNWANTED_TAGS):
            tag.decompose()

        raw_text = copy_soup.get_text(separator="\n")
        # Collapse excessive whitespace
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
        clean_text = "\n".join(line for line in lines if line)
        return clean_text
