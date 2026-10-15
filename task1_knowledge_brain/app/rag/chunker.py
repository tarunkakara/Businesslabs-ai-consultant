"""Document Chunker with Multimodal Image Attachment."""

import hashlib
import json
from typing import Any, Dict, List


class DocumentChunk:
    """Represents a chunk ready for vector embedding."""

    def __init__(
        self,
        chunk_id: str,
        text: str,
        metadata: Dict[str, Any],
    ):
        self.chunk_id = chunk_id
        self.text = text
        self.metadata = metadata

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "metadata": self.metadata,
        }


class DocumentChunker:
    """Splits article text into semantically cohesive chunks and associates article images."""

    def __init__(self, target_chunk_size: int = 500, chunk_overlap: int = 80):
        self.target_chunk_size = target_chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_article(
        self,
        url: str,
        title: str,
        breadcrumbs: str,
        body_text: str,
        images: List[Dict[str, Any]],
    ) -> List[DocumentChunk]:
        """Split article text into chunks and link image metadata."""
        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
        paragraphs = [p.strip() for p in body_text.split("\n") if p.strip()]

        if not paragraphs:
            # Fallback if text is minimal
            if not body_text.strip():
                return []
            paragraphs = [body_text.strip()]

        raw_chunks: List[str] = []
        current_chunk = ""

        for p in paragraphs:
            if not current_chunk:
                current_chunk = p
            elif len(current_chunk) + len(p) + 1 <= self.target_chunk_size:
                current_chunk += "\n" + p
            else:
                raw_chunks.append(current_chunk)
                # Overlap logic
                overlap_text = current_chunk[-self.chunk_overlap:] if len(current_chunk) > self.chunk_overlap else ""
                current_chunk = (overlap_text + "\n" + p).strip()

        if current_chunk:
            raw_chunks.append(current_chunk)

        images_json = json.dumps(images[:5]) if images else "[]"

        chunks: List[DocumentChunk] = []
        for idx, text in enumerate(raw_chunks):
            # Prepend context header for enhanced retrieval relevance
            full_text = f"Title: {title}\nCategory: {breadcrumbs}\n\n{text}"
            chunk_id = f"{url_hash}_{idx}"
            metadata = {
                "url": url,
                "title": title,
                "breadcrumbs": breadcrumbs,
                "chunk_index": idx,
                "total_chunks": len(raw_chunks),
                "images_json": images_json,
            }
            chunks.append(DocumentChunk(chunk_id=chunk_id, text=full_text, metadata=metadata))

        return chunks
