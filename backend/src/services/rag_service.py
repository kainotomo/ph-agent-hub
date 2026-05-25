# =============================================================================
# PH Agent Hub — RAG Service (Issue #250)
# =============================================================================
# Document ingestion, semantic search, and management for the "Chat with
# your documents" feature. Uses MariaDB JSON columns for embedding storage
# with Python cosine similarity — no external vector DB required.
#
# Architecture:
#   1. FileUpload.extracted_text is pre-populated by upload_service
#   2. index_document() chunks, embeds, and stores in rag_documents
#   3. search_documents() loads embeddings, computes similarity in Python
#   4. For production at scale, swap embedding_json for pgvector/Qdrant
# =============================================================================

import logging
import math
from datetime import datetime, timezone

from sqlalchemy import select, delete, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.exceptions import NotFoundError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_EMBEDDING_MODEL: str = "text-embedding-3-small"
DEFAULT_CHUNK_SIZE: int = 500
DEFAULT_CHUNK_OVERLAP: int = 50
DEFAULT_TOP_K: int = 5
DEFAULT_MIN_SCORE: float = 0.30


# ---------------------------------------------------------------------------
# Chunking (delegate to rag_search)
# ---------------------------------------------------------------------------

def _chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split text into overlapping chunks.

    Delegates to the same chunker used by the RAG search tool.
    """
    try:
        from ..tools.rag_search import _chunk_text as _rag_chunk
        return _rag_chunk(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    except Exception as exc:
        logger.warning("Chunking via rag_search failed: %s", exc)
        # Fallback: simple paragraph split
        if not text or not text.strip():
            return []
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[str] = []
        current = ""
        for p in paragraphs:
            if len(current) + len(p) + 2 <= chunk_size:
                current = (current + "\n\n" + p) if current else p
            else:
                if current:
                    chunks.append(current)
                current = p
        if current:
            chunks.append(current)
        return chunks if chunks else [text]


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

async def _get_embeddings(
    texts: list[str],
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> list[list[float]]:
    """Get embeddings for a list of texts.

    Delegates to the same embedding client used by the RAG search tool.
    """
    if not texts:
        return []

    try:
        from ..tools.rag_search import _get_embeddings as _rag_embed
        return await _rag_embed(texts, model=model)
    except Exception as exc:
        logger.warning("Embedding via rag_search failed: %s", exc)
        return []


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def index_document(
    db: AsyncSession,
    file_upload,
    embedding_model: str | None = None,
) -> int:
    """Index a file upload as RAG document chunks.

    Takes a ``FileUpload`` ORM object (with ``extracted_text`` already
    populated), chunks the text, generates embeddings, and persists
    each chunk as a ``RAGDocument`` row.

    Idempotent: deletes any existing chunks for the same ``file_id``
    before re-indexing.

    Args:
        db: Active async DB session.
        file_upload: A ``FileUpload`` ORM instance.
        embedding_model: Embedding model name (default: ``text-embedding-3-small``).

    Returns:
        Number of chunks indexed.
    """
    from ..db.orm.rag import RAGDocument

    text = getattr(file_upload, "extracted_text", None)
    if not text or not text.strip():
        logger.info("No extracted text for file %s — skipping RAG indexing", file_upload.id)
        return 0

    # De-duplicate: remove existing chunks for this file_id
    await db.execute(
        delete(RAGDocument).where(RAGDocument.file_id == file_upload.id)
    )

    model = embedding_model or DEFAULT_EMBEDDING_MODEL

    # Chunk
    chunks = _chunk_text(text)
    if not chunks:
        logger.info("No chunks produced for file %s", file_upload.id)
        return 0

    # Embed
    embeddings = await _get_embeddings(chunks, model=model)
    if not embeddings or len(embeddings) != len(chunks):
        logger.warning(
            "Embedding mismatch for file %s: got %d embeddings for %d chunks",
            file_upload.id,
            len(embeddings or []),
            len(chunks),
        )
        return 0

    # Persist each chunk
    for i, (chunk_text, emb) in enumerate(zip(chunks, embeddings)):
        if not emb:
            continue
        doc_id = f"{file_upload.id}_{i}"
        row = RAGDocument(
            id=doc_id,
            tenant_id=file_upload.tenant_id,
            file_id=file_upload.id,
            title=file_upload.original_filename or f"chunk_{i}",
            content=chunk_text,
            chunk_index=i,
            extra_metadata={
                "original_filename": file_upload.original_filename,
                "content_type": file_upload.content_type,
                "size_bytes": file_upload.size_bytes,
                "chunk_count": len(chunks),
            },
            embedding_json=emb,
            model=model,
        )
        db.add(row)

    await db.commit()

    logger.info(
        "Indexed %d chunks for file %s (tenant %s)",
        len(chunks),
        file_upload.id,
        file_upload.tenant_id,
    )
    return len(chunks)


async def delete_document(db: AsyncSession, file_id: str) -> int:
    """Delete all RAG document chunks for a given file upload.

    Args:
        db: Active async DB session.
        file_id: The ``FileUpload.id`` whose chunks should be removed.

    Returns:
        Number of deleted rows.
    """
    from ..db.orm.rag import RAGDocument

    result = await db.execute(
        select(sa_func.count()).select_from(RAGDocument).where(
            RAGDocument.file_id == file_id
        )
    )
    count = result.scalar() or 0

    await db.execute(
        delete(RAGDocument).where(RAGDocument.file_id == file_id)
    )
    await db.commit()

    if count:
        logger.info("Deleted %d RAG chunks for file %s", count, file_id)
    return count


async def search_documents(
    db: AsyncSession,
    query: str,
    tenant_id: str,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
    file_ids: list[str] | None = None,
) -> list[dict]:
    """Semantic search across indexed document chunks for a tenant.

    Generates an embedding for the query, loads all stored embeddings
    for the tenant (optionally filtered by ``file_ids``), computes
    cosine similarity in Python, and returns the top-k results.

    Args:
        db: Active async DB session.
        query: The search query text.
        tenant_id: Tenant UUID to scope the search.
        top_k: Maximum results to return (default 5).
        min_score: Minimum cosine similarity threshold (default 0.30).
        file_ids: Optional list of file IDs to restrict search scope.

    Returns:
        A list of dicts, each with keys:
        - ``text``: the chunk text
        - ``score``: cosine similarity (0.0–1.0)
        - ``file_id``: source file upload UUID
        - ``filename``: original filename
        - ``chunk_index``: position within the source document
    """
    from ..db.orm.rag import RAGDocument

    if not query or not query.strip():
        return []

    # Generate query embedding
    query_emb = await _get_embeddings([query])
    if not query_emb or not query_emb[0]:
        logger.warning("Failed to generate query embedding for RAG search")
        return []

    query_vector = query_emb[0]

    # Load stored embeddings
    stmt = select(RAGDocument).where(RAGDocument.tenant_id == tenant_id)

    if file_ids:
        stmt = stmt.where(RAGDocument.file_id.in_(file_ids))

    result = await db.execute(stmt)
    docs = list(result.scalars().all())

    if not docs:
        return []

    # Score and rank
    scored: list[tuple[float, dict]] = []
    for doc in docs:
        if not doc.embedding_json:
            continue
        sim = _cosine_similarity(query_vector, doc.embedding_json)
        if sim < min_score:
            continue
        scored.append((
            sim,
            {
                "text": doc.content,
                "score": round(sim, 4),
                "file_id": doc.file_id,
                "filename": doc.extra_metadata.get("original_filename", doc.title)
                if doc.extra_metadata else doc.title,
                "chunk_index": doc.chunk_index,
            },
        ))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [item[1] for item in scored[:top_k]]


async def get_document_count(db: AsyncSession, tenant_id: str) -> int:
    """Return the total number of RAG chunks for a tenant."""
    from ..db.orm.rag import RAGDocument

    result = await db.execute(
        select(sa_func.count()).select_from(RAGDocument).where(
            RAGDocument.tenant_id == tenant_id
        )
    )
    return result.scalar() or 0


async def list_documents(
    db: AsyncSession,
    tenant_id: str,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int]:
    """List indexed documents grouped by source file.

    Returns deduplicated documents (one entry per ``file_id``) with
    title, chunk count, and created date, plus total count for
    pagination.

    Args:
        db: Active async DB session.
        tenant_id: Tenant UUID to scope the listing.
        page: 1-indexed page number.
        page_size: Items per page.

    Returns:
        A tuple of (items list, total_items_count).
    """
    from ..db.orm.rag import RAGDocument

    # Get unique file_ids with their metadata
    stmt = (
        select(
            RAGDocument.file_id,
            RAGDocument.title,
            RAGDocument.extra_metadata,
            sa_func.count().label("chunk_count"),
            sa_func.max(RAGDocument.created_at).label("created_at"),
        )
        .where(
            RAGDocument.tenant_id == tenant_id,
            RAGDocument.file_id.isnot(None),
        )
        .group_by(RAGDocument.file_id, RAGDocument.title, RAGDocument.extra_metadata)
        .order_by(sa_func.max(RAGDocument.created_at).desc())
    )

    # Count total
    count_stmt = select(sa_func.count()).select_from(stmt.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    # Paginate
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)
    result = await db.execute(stmt)
    rows = result.all()

    items = [
        {
            "file_id": row.file_id,
            "title": row.title,
            "original_filename": row.extra_metadata.get("original_filename")
            if row.extra_metadata else row.title,
            "content_type": row.extra_metadata.get("content_type")
            if row.extra_metadata else None,
            "chunk_count": row.chunk_count,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]

    return items, total
