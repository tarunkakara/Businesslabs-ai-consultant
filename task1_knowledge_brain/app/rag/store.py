"""ChromaDB Persistent Vector Store for Article Chunks with Low-Memory Embeddings."""

import hashlib
import json
import logging
import math
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from task1_knowledge_brain.app.config import settings
from task1_knowledge_brain.app.rag.chunker import DocumentChunk

logger = logging.getLogger("knowledge_brain.rag.store")


STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but",
    "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for",
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself",
    "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just",
    "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once",
    "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she",
    "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves",
    "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "will", "with", "would", "you", "your", "yours", "yourself", "yourselves"
}


class FastCompactEmbeddingFunction(EmbeddingFunction[Documents]):
    """Memory-efficient, zero-weight deterministic feature-hashing embedding function.
    Requires no ONNX runtime, no PyTorch, no heavy external model weights (<1MB RAM).
    Produces 384-dimensional L2-normalized vector embeddings based on clean word unigrams,
    stemming, bigrams, and character trigrams for fast, memory-safe, grounded semantic retrieval.
    """

    def __init__(self, dimension: int = 384):
        self.dimension = dimension

    def _tokenize(self, text: str) -> List[str]:
        raw = re.findall(r'[a-zA-Z0-9]+', text.lower())
        tokens = []
        for w in raw:
            tokens.append(w)
            if len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
                tokens.append(w[:-1])
        return tokens

    def _embed_single(self, text: str) -> List[float]:
        vec = [0.0] * self.dimension
        if not text:
            return vec

        tokens = self._tokenize(text)
        if not tokens:
            return vec

        for i, word in enumerate(tokens):
            is_stop = word in STOP_WORDS
            weight = 0.1 if is_stop else (2.0 if i < 15 else 1.2)

            h = int(hashlib.md5(word.encode("utf-8")).hexdigest()[:8], 16)
            idx = h % self.dimension
            sign = 1.0 if ((h >> 8) & 1) else -1.0
            vec[idx] += sign * weight

            if not is_stop and len(word) >= 3:
                for j in range(len(word) - 2):
                    trigram = word[j:j+3]
                    h_tri = int(hashlib.md5(trigram.encode("utf-8")).hexdigest()[:8], 16)
                    idx_tri = h_tri % self.dimension
                    sign_tri = 1.0 if ((h_tri >> 8) & 1) else -1.0
                    vec[idx_tri] += 0.5 * sign_tri * weight

        content_words = [w for w in tokens if w not in STOP_WORDS]
        for i in range(len(content_words) - 1):
            bg = f"{content_words[i]}_{content_words[i+1]}"
            h_bi = int(hashlib.md5(bg.encode("utf-8")).hexdigest()[:8], 16)
            idx_bi = h_bi % self.dimension
            sign_bi = 1.0 if ((h_bi >> 8) & 1) else -1.0
            vec[idx_bi] += 0.8 * sign_bi

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            return [round(x / norm, 6) for x in vec]
        return vec

    @staticmethod
    def name() -> str:
        return "fast_compact_embedding_function"

    def get_config(self) -> Dict[str, Any]:
        return {"dimension": self.dimension}

    @classmethod
    def build_from_config(cls, config: Dict[str, Any]) -> "FastCompactEmbeddingFunction":
        return cls(dimension=config.get("dimension", 384))

    def __call__(self, input: Documents) -> Embeddings:
        return [self._embed_single(t) for t in input]


class RAGVectorStore:
    """Manages vector embeddings, article chunk indexing, purging, and similarity search."""

    COLLECTION_NAME = "brilliant_directories_knowledge"
    EMBEDDING_VERSION = "v2_fast_compact"
    _instances: Dict[str, "RAGVectorStore"] = {}

    def __new__(cls, persist_directory: Optional[str] = None, *args, **kwargs):
        dir_key = str(Path(persist_directory or settings.chroma_persist_directory).resolve())
        if dir_key not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[dir_key] = instance
            instance._initialized = False
        return cls._instances[dir_key]

    def __init__(self, persist_directory: Optional[str] = None):
        if getattr(self, "_initialized", False):
            return

        self.persist_directory = str(Path(persist_directory or settings.chroma_persist_directory).resolve())
        Path(self.persist_directory).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=self.persist_directory)
        self.embedding_function = FastCompactEmbeddingFunction()

        try:
            self.collection = self.client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                embedding_function=self.embedding_function,
                metadata={
                    "description": "Brilliant Directories Solution Articles",
                    "embedding_version": self.EMBEDDING_VERSION,
                }
            )
            meta = self.collection.metadata or {}
            if meta.get("embedding_version") != self.EMBEDDING_VERSION:
                logger.info("Migrating vector collection to %s embedding space...", self.EMBEDDING_VERSION)
                self.reset_collection()
        except Exception as exc:
            logger.warning("Error getting collection, recreating: %s", exc)
            self.reset_collection()

        self._initialized = True

    @classmethod
    def clear_instances(cls) -> None:
        """Clear cached singleton instances (useful for test isolation)."""
        cls._instances.clear()

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
            # Therefore: raw_cos = 1.0 - (dist / 2.0).
            # For sparse feature-hashed embeddings, we calibrate raw cosine similarity
            # into a normalized relevance score [0.0, 1.0] with safe scaling:
            raw_cos = max(0.0, 1.0 - (float(dist) / 2.0))
            similarity = min(1.0, raw_cos * 1.25)

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
        try:
            self.client.delete_collection(self.COLLECTION_NAME)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=self.embedding_function,
            metadata={
                "description": "Brilliant Directories Solution Articles",
                "embedding_version": self.EMBEDDING_VERSION,
            }
        )
