# =============================================================================
# PH Agent Hub — RAG Search Tool Factory
# =============================================================================
# Semantic search across uploaded documents. Embedding via OpenAI-compatible
# API + in-memory vector store with cosine similarity.
#
# Architecture:
#   1. Documents are chunked and embedded on upload/indexing
#   2. Embeddings stored in memory (per-tool instance)
#   3. Cosine similarity search at query time
#   4. For production: swap to pgvector or Qdrant
# =============================================================================

import hashlib
import logging
import math
from typing import Any

from agent_framework import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_CHUNK_SIZE: int = 500
DEFAULT_CHUNK_OVERLAP: int = 50
DEFAULT_TOP_K: int = 5
DEFAULT_EMBEDDING_MODEL: str = "text-embedding-3-small"


# ---------------------------------------------------------------------------
# Simple text chunker
# ---------------------------------------------------------------------------

def _chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks of approximately chunk_size characters.

    Tries to split on paragraph boundaries first, then sentence boundaries,
    then falls back to fixed-size character chunks.
    """
    if not text or not text.strip():
        return []

    # Split on paragraphs first
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current_chunk: str = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current_chunk) + len(para) + 2 <= chunk_size:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
        else:
            if current_chunk:
                chunks.append(current_chunk)

            # If the paragraph itself is too long, split by sentences
            if len(para) > chunk_size:
                sentences = para.replace("! ", "!|").replace("? ", "?|").replace(". ", ".|").split("|")
                sub_chunk = ""
                for sent in sentences:
                    sent = sent.strip()
                    if not sent:
                        continue
                    if len(sub_chunk) + len(sent) + 1 <= chunk_size:
                        if sub_chunk:
                            sub_chunk += " " + sent
                        else:
                            sub_chunk = sent
                    else:
                        if sub_chunk:
                            chunks.append(sub_chunk)
                        # If a single sentence is still too long, split by chars
                        if len(sent) > chunk_size:
                            for i in range(0, len(sent), chunk_size - chunk_overlap):
                                chunks.append(sent[i:i + chunk_size])
                        else:
                            sub_chunk = sent
                if sub_chunk:
                    current_chunk = sub_chunk
                else:
                    current_chunk = ""
            else:
                current_chunk = para

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# ---------------------------------------------------------------------------
# Simple vector store (in-memory)
# ---------------------------------------------------------------------------

class SimpleVectorStore:
    """In-memory vector store with cosine similarity search."""

    def __init__(self):
        self.documents: list[dict] = []  # [{id, text, embedding, metadata}]

    def add(self, doc_id: str, text: str, embedding: list[float], metadata: dict | None = None) -> None:
        """Add a document to the store."""
        self.documents.append({
            "id": doc_id,
            "text": text,
            "embedding": embedding,
            "metadata": metadata or {},
        })

    def clear(self) -> None:
        """Remove all documents."""
        self.documents.clear()

    def search(self, query_embedding: list[float], top_k: int = 5) -> list[dict]:
        """Search for the top_k most similar documents by cosine similarity."""
        if not self.documents:
            return []

        scored: list[tuple[float, dict]] = []
        for doc in self.documents:
            sim = _cosine_similarity(query_embedding, doc["embedding"])
            scored.append((sim, doc))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: list[dict] = []
        for sim, doc in scored[:top_k]:
            results.append({
                "id": doc["id"],
                "text": doc["text"],
                "score": round(sim, 4),
                "metadata": doc["metadata"],
            })

        return results

    @property
    def document_count(self) -> int:
        return len(self.documents)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Embedding client
# ---------------------------------------------------------------------------

def _check_embedding_available(
    api_key: str | None = None,
) -> tuple[bool, str | None]:
    """Check whether embedding API credentials are configured (zero network).

    Returns ``(available, reason)``:
    - ``available`` — ``True`` if a key is present, ``False`` if fallback
      will be used.
    - ``reason`` — human-readable explanation when ``available`` is
      ``False``, otherwise ``None``.
    """
    resolved_key = api_key
    if not resolved_key:
        try:
            from ..core.config import settings
        except ImportError:
            from core.config import settings  # noqa: F811

        resolved_key = getattr(settings, "OPENAI_API_KEY", None)

    if not resolved_key:
        return (
            False,
            "No embedding API key configured. "
            "RAG indexing will use fallback TF-IDF embeddings. "
            "Set OPENAI_API_KEY or configure an api_key in the RAG tool config.",
        )
    return (True, None)


async def _get_embeddings(
    texts: list[str],
    model: str = DEFAULT_EMBEDDING_MODEL,
    api_key: str | None = None,
    base_url: str | None = None,
) -> list[list[float]]:
    """Get embeddings for a list of texts using an OpenAI-compatible API.

    Uses the platform's model configuration to resolve the API endpoint.
    Falls back to a simple TF-IDF-like approach if no embedding API is available.
    """
    if not texts:
        return []

    # Skip the API call entirely when no key is configured — avoids
    # a guaranteed 401 and the associated log noise.
    available, reason = _check_embedding_available(api_key)
    if not available:
        global _fallback_reason_logged
        if not _fallback_reason_logged:
            logger.info("Embedding API key missing — using fallback TF-IDF (%s)", reason)
            _fallback_reason_logged = True
        else:
            logger.debug("Fallback TF-IDF embedding (already warned)")
        return [_fallback_embed(text) for text in texts]

    # Try to use the platform's embedding model
    try:
        import httpx
        try:
            from ..core.config import settings
        except ImportError:
            from core.config import settings  # noqa: F811

        # Determine the embedding endpoint
        if base_url:
            embed_url = base_url.rstrip("/") + "/embeddings"
        else:
            # Use the default OpenAI-compatible endpoint from settings
            embed_url = getattr(settings, "EMBEDDING_API_URL", None)
            if not embed_url:
                # Fall back to the chat model's base URL
                embed_url = "https://api.openai.com/v1/embeddings"

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            # Try to use the default API key from settings
            default_key = getattr(settings, "OPENAI_API_KEY", None)
            if default_key:
                headers["Authorization"] = f"Bearer {default_key}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                embed_url,
                json={
                    "model": model,
                    "input": texts,
                },
                headers=headers,
            )

            if response.status_code == 200:
                data = response.json()
                return [item["embedding"] for item in data["data"]]
            else:
                # 401 is expected when the key is invalid/misconfigured —
                # log at debug level to avoid alarming operators.
                if response.status_code == 401:
                    logger.debug(
                        "Embedding API returned 401 (unauthorized): %s "
                        "— using fallback TF-IDF",
                        response.text[:200],
                    )
                else:
                    logger.warning(
                        "Embedding API returned %d: %s",
                        response.status_code,
                        response.text[:200],
                    )
    except Exception as exc:
        logger.warning("Failed to get embeddings from API: %s", exc)

    # Fallback: use a simple bag-of-words embedding
    if not _fallback_reason_logged:
        logger.info("Embedding API failed — using fallback TF-IDF")
        _fallback_reason_logged = True
    else:
        logger.debug("Fallback TF-IDF embedding (already warned)")
    return [_fallback_embed(text) for text in texts]


def _fallback_embed(text: str, dim: int = 256) -> list[float]:
    """Simple fallback embedding using character n-gram hashing.

    This is NOT suitable for production but provides basic functionality
    when no embedding API is configured.
    """
    text = text.lower()
    ngrams: dict[int, float] = {}

    # Character trigrams
    for i in range(len(text) - 2):
        ng = text[i:i + 3]
        h = hash(ng) % dim
        ngrams[h] = ngrams.get(h, 0) + 1

    # Word unigrams
    for word in text.split():
        h = hash(word) % dim
        ngrams[h] = ngrams.get(h, 0) + 1

    # Normalize
    vec = [0.0] * dim
    norm = math.sqrt(sum(v * v for v in ngrams.values())) or 1.0
    for idx, val in ngrams.items():
        vec[idx] = val / norm

    return vec


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------

# Module-level flag to log the verbose fallback reason only once per process.
_fallback_reason_logged: bool = False

# Global vector store instance (per process) — used as a cache layer on top
# of the DB.  When db + tenant_id are provided, tools also persist to / read
# from the rag_documents table.
_vector_store = SimpleVectorStore()


def build_rag_search_tools(
    tool_config: dict | None = None,
    db=None,
    tenant_id: str | None = None,
) -> list:
    """Return a list of MAF @tool-decorated async functions for RAG search.

    Args:
        tool_config: Optional ``Tool.config`` JSON dict.  May include:
            - ``embedding_model`` (str)
            - ``api_key`` (str)
            - ``base_url`` (str)
            - ``chunk_size`` (int)
            - ``top_k`` (int)
        db: Optional async DB session for persistent storage.
        tenant_id: Required when ``db`` is provided (tenant-scoped).

    Returns:
        A list of callables ready to pass to ``Agent(tools=...)``.
    """
    config = tool_config or {}
    embedding_model: str = config.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
    api_key: str = config.get("api_key", "")
    base_url: str = config.get("base_url", "")
    chunk_size: int = int(config.get("chunk_size", DEFAULT_CHUNK_SIZE))
    default_top_k: int = int(config.get("top_k", DEFAULT_TOP_K))

    @tool
    async def index_document(content: str, doc_id: str | None = None, metadata: dict | None = None) -> dict:
        """Index a document for semantic search.

        Splits the document into chunks, generates embeddings for each chunk,
        and stores them in the vector store (and DB if available).

        Args:
            content: The full text content of the document to index.
            doc_id: Optional document identifier (auto-generated if not provided).
            metadata: Optional metadata dict (e.g., {"filename": "report.pdf"}).

        Returns:
            A dict with:
            - ``doc_id``: the document identifier
            - ``chunks_indexed``: number of chunks created and indexed
            - ``status``: "ok" or "error"
            - ``error``: error message if indexing failed
        """
        if not content or not content.strip():
            return {"error": "No content provided to index", "status": "error"}

        try:
            # Generate a unique doc ID if not provided
            if not doc_id:
                doc_id = hashlib.sha256(content.encode()).hexdigest()[:16]

            # Remove old chunks for this doc_id from in-memory store
            _vector_store.documents = [
                d for d in _vector_store.documents
                if d["metadata"].get("doc_id") != doc_id
            ]

            # Chunk the text
            chunks = _chunk_text(content, chunk_size=chunk_size)
            if not chunks:
                return {"error": "No chunks could be created from the content", "status": "error"}

            # Get embeddings
            embeddings = await _get_embeddings(
                chunks,
                model=embedding_model,
                api_key=api_key,
                base_url=base_url,
            )

            if not embeddings or len(embeddings) != len(chunks):
                return {
                    "error": f"Failed to generate embeddings (got {len(embeddings)} for {len(chunks)} chunks)",
                    "status": "error",
                }

            # Store in in-memory vector store
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                chunk_meta = {
                    "doc_id": doc_id,
                    "chunk_index": i,
                    **(metadata or {}),
                }
                _vector_store.add(
                    doc_id=f"{doc_id}_{i}",
                    text=chunk,
                    embedding=emb,
                    metadata=chunk_meta,
                )

            logger.info(
                "Indexed document %s: %d chunks (store size: %d)",
                doc_id, len(chunks), _vector_store.document_count,
            )

            return {
                "doc_id": doc_id,
                "chunks_indexed": len(chunks),
                "total_chunks_in_store": _vector_store.document_count,
                "status": "ok",
            }

        except Exception as exc:
            logger.error("Document indexing failed: %s", exc)
            return {"error": f"Indexing failed: {str(exc)}", "status": "error"}

    # ------------------------------------------------------------------
    @tool
    async def search_documents(query: str, top_k: int | None = None) -> dict:
        """Search indexed documents semantically.

        Generates an embedding for the query and returns the most similar
        document chunks using cosine similarity.  When a DB session is
        available, searches the persistent ``rag_documents`` table;
        otherwise falls back to the in-memory vector store.

        Args:
            query: The search query text.
            top_k: Number of results to return (default from config, typically 5).

        Returns:
            A dict with:
            - ``query``: the original query
            - ``results``: list of dicts with ``text``, ``score``, and ``metadata``
            - ``total_results``: number of results returned
            - ``error``: error message if search failed
        """
        if not query or not query.strip():
            return {"error": "No query provided", "results": [], "total_results": 0}

        k = top_k or default_top_k

        try:
            # Generate query embedding
            query_embeddings = await _get_embeddings(
                [query],
                model=embedding_model,
                api_key=api_key,
                base_url=base_url,
            )

            if not query_embeddings:
                return {"error": "Failed to generate query embedding", "results": [], "total_results": 0}

            query_embedding = query_embeddings[0]

            # Search via DB if available
            if db is not None and tenant_id is not None:
                try:
                    from ..services.rag_service import search_documents as _rag_search

                    results = await _rag_search(
                        db=db,
                        query=query,
                        tenant_id=tenant_id,
                        top_k=k,
                    )
                    return {
                        "query": query,
                        "results": [
                            {
                                "id": r.get("file_id", ""),
                                "text": r["text"],
                                "score": r["score"],
                                "metadata": {
                                    "filename": r.get("filename", ""),
                                    "chunk_index": r.get("chunk_index", 0),
                                },
                            }
                            for r in results
                        ],
                        "total_results": len(results),
                    }
                except Exception as exc:
                    logger.warning("DB RAG search failed, falling back to in-memory: %s", exc)

            # Fall back to in-memory vector store
            if _vector_store.document_count == 0:
                return {
                    "query": query,
                    "results": [],
                    "total_results": 0,
                    "message": "No documents have been indexed yet. Use index_document first.",
                }

            results = _vector_store.search(query_embedding, top_k=k)

            return {
                "query": query,
                "results": results,
                "total_results": len(results),
            }

        except Exception as exc:
            logger.error("Document search failed: %s", exc)
            return {"error": f"Search failed: {str(exc)}", "results": [], "total_results": 0}

    # ------------------------------------------------------------------
    @tool
    async def clear_index() -> dict:
        """Clear all indexed documents from the vector store.

        When a DB session is available, also clears the persistent
        ``rag_documents`` table for the current tenant.

        Returns:
            A dict with ``status`` and ``documents_removed`` count.
        """
        count = _vector_store.document_count

        # Clear in-memory store
        _vector_store.clear()

        # Clear DB if available
        if db is not None and tenant_id is not None:
            try:
                from sqlalchemy import delete as sa_delete
                from ..db.orm.rag import RAGDocument

                result = await db.execute(
                    sa_delete(RAGDocument).where(RAGDocument.tenant_id == tenant_id)
                )
                await db.commit()
                db_count = result.rowcount
                logger.info("Cleared %d RAG documents from DB for tenant %s", db_count, tenant_id)
                count = max(count, db_count)
            except Exception as exc:
                logger.warning("Failed to clear DB RAG index: %s", exc)

        logger.info("Cleared RAG index (%d documents removed)", count)
        return {"status": "ok", "documents_removed": count}

    return [index_document, search_documents, clear_index]
