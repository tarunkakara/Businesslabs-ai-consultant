"""ChromaDB Persistent Vector Store for Article Chunks."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import chromadb

from task1_knowledge_brain.app.config import settings
from task1_knowledge_brain.app.rag.chunker import DocumentChunk

logger = logging.getLogger("knowledge_brain.rag.store")


class RAGVectorStore:
    """Manages vector embeddings, article chunk indexing, purging, and similarity search."""

    COLLECTION_NAME = "brilliant_directories_knowledge"

    def __init__(self, persist_directory: Optional[str] = None):
        self.persist_directory = persist_directory or settings.chroma_persist_directory
        Path(self.persist_directory).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.persist_directory)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "Brilliant Directories Solution Articles"}
        )

    def add_article_chunks(self, url: str, chunks: List[DocumentChunk]) -> int:
        """Idempotently add chunks for an article, replacing existing chunks."""
        self.purge_article(url)

        if not chunks:
            return 0

        documents = [c.text for c in chunks]
        metadatas = [c.metadata for c in chunks]
        ids = [c.chunk_id for c in chunks]

        self.collection.add(
            documents=documents,
            metadatas=metadatas,
            ids=ids,
        )
        logger.debug("Indexed %d chunks for: %s", len(chunks), url)
        return len(chunks)

    def purge_article(self, url: str) -> int:
        """Purge all chunks associated with an article URL (ensures removed articles disappear)."""
        try:
            existing = self.collection.get(where={"url": url})
            if existing and existing.get("ids"):
                self.collection.delete(ids=existing["ids"])
                logger.info("Purged %d chunks for removed/updated article: %s", len(existing["ids"]), url)
                return len(existing["ids"])
        except Exception as exc:
            logger.warning("Error checking or purging chunks for %s: %s", url, exc)
        return 0

    def search(self, query_text: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Retrieve most relevant chunks with similarity score calculation."""
        count = self.collection.count()
        if count == 0:
            return []

        limit = min(top_k, count)
        results = self.collection.query(
            query_texts=[query_text],
            n_results=limit,
        )

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0] if results.get("distances") else [0.5] * len(documents)

        retrieved: List[Dict[str, Any]] = []
        for doc, meta, dist in zip(documents, metadatas, distances):
            # ChromaDB default space is squared L2 distance between normalized embeddings:
            # dist = ||u - v||^2 = 2 - 2*cos(theta).
            # Therefore: similarity = 1.0 - (dist / 2.0) = cos(theta).
            # We clamp to [0.0, 1.0] to obtain a clean normalized relevance score.
            similarity = max(0.0, min(1.0, 1.0 - (float(dist) / 2.0)))

            images_raw = meta.get("images_json", "[]")
            try:
                images = json.loads(images_raw)
            except Exception:
                images = []

            retrieved.append({
                "title": meta.get("title", "Article"),
                "url": meta.get("url", ""),
                "breadcrumbs": meta.get("breadcrumbs", ""),
                "text": doc,
                "similarity_score": round(similarity, 4),
                "image_urls": images,
            })

        # Sort descending by similarity
        retrieved.sort(key=lambda x: x["similarity_score"], reverse=True)
        return retrieved

    def count(self) -> int:
        """Return total indexed chunks."""
        return self.collection.count()

    def reset_collection(self) -> None:
        """Clear all indexed documents."""
        self.client.delete_collection(self.COLLECTION_NAME)
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"description": "Brilliant Directories Solution Articles"}
        )
