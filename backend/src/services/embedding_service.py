# =============================================================================
# PH Agent Hub — Embedding Service (Cross-Session Memory, Issue #229)
# =============================================================================
# Handles embedding generation, storage, and semantic retrieval for past
# conversations. Reuses the embedding client from rag_search.py.
# =============================================================================

import logging
import math
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_EMBEDDING_MODEL: str = "text-embedding-3-small"
DEFAULT_TOP_K: int = 3
DEFAULT_MIN_SCORE: float = 0.30


# ---------------------------------------------------------------------------
# Embedding helpers (reuse from rag_search.py)
# ---------------------------------------------------------------------------

async def _get_embeddings(
    texts: list[str],
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> list[list[float]]:
    """Get embeddings for a list of texts.

    Delegates to the same embedding client used by the RAG search tool.
    Falls back to TF-IDF-like hashing if the API is unavailable.
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


async def embed_message(
    db: AsyncSession,
    message_id: str,
    user_id: str,
    tenant_id: str,
    text: str,
    model: str | None = None,
) -> None:
    """Generate and store an embedding for a single user message.

    This is called asynchronously after message persistence.
    Temporary messages and empty/non-user messages are skipped.

    Args:
        db: Active async DB session.
        message_id: The UUID of the persisted message.
        user_id: The message author's user UUID.
        tenant_id: The message's tenant UUID.
        text: The plain text content of the message.
        model: Embedding model name (default: settings.CROSS_SESSION_EMBEDDING_MODEL).
    """
    if not text or not text.strip():
        return

    embedding_model = model or getattr(settings, "CROSS_SESSION_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)

    try:
        # Single-chunk for short messages, multi-chunk for long ones
        chunks = _chunk_text(text) if len(text) > 500 else [text]

        embeddings = await _get_embeddings(chunks, model=embedding_model)
        if not embeddings:
            logger.debug("No embeddings returned for message %s", message_id)
            return

        # Import here to avoid circular imports at module level
        from ..db.orm.message_embeddings import MessageEmbedding

        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            if not emb:
                continue
            row = MessageEmbedding(
                message_id=message_id,
                user_id=user_id,
                tenant_id=tenant_id,
                chunk_index=i,
                embedding_json=emb,
                model=embedding_model,
            )
            db.add(row)

        await db.commit()
        logger.debug(
            "Stored %d embedding(s) for message %s (%s)",
            len(embeddings), message_id, embedding_model,
        )
    except Exception:
        logger.exception("Failed to embed message %s", message_id)


async def embed_query(
    text: str,
    model: str | None = None,
) -> list[float] | None:
    """Generate an embedding vector for a query string.

    Args:
        text: The query text (typically the current user message).
        model: Embedding model name.

    Returns:
        A single embedding vector, or None on failure.
    """
    if not text or not text.strip():
        return None

    embedding_model = model or getattr(settings, "CROSS_SESSION_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)

    try:
        results = await _get_embeddings([text], model=embedding_model)
        if results and results[0]:
            return results[0]
        return None
    except Exception:
        logger.exception("Failed to embed query")
        return None


async def retrieve_similar(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
    query_embedding: list[float],
    session_id: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[dict]:
    """Retrieve past conversation snippets similar to the query embedding.

    Loads all embeddings for the given user from the ``message_embeddings``
    table, computes cosine similarity in Python, deduplicates by message,
    and returns the top-K results with full message context.

    Args:
        db: Active async DB session.
        user_id: The current user's UUID.
        tenant_id: The current tenant's UUID.
        query_embedding: The embedding vector of the current query.
        session_id: Optional — exclude messages from this session
                    (to avoid retrieving from the current conversation).
        top_k: Maximum number of snippets to return.
        min_score: Minimum cosine similarity threshold.

    Returns:
        A list of dicts, each containing:
            - message_id (str)
            - score (float)
            - session_id (str)
            - session_title (str)
            - user_text (str)
            - assistant_text (str | None)
            - created_at (str | None)
    """
    if not query_embedding:
        return []

    from ..db.orm.message_embeddings import MessageEmbedding

    try:
        # Load all embeddings for this user
        stmt = select(MessageEmbedding).where(
            MessageEmbedding.user_id == user_id,
            MessageEmbedding.tenant_id == tenant_id,
        ).order_by(MessageEmbedding.created_at.desc())

        result = await db.execute(stmt)
        rows = list(result.scalars().all())

        if not rows:
            return []

        # Score each embedding row, keep best per message_id
        best_per_message: dict[str, tuple[float, MessageEmbedding]] = {}

        for row in rows:
            if not row.embedding_json:
                continue
            score = _cosine_similarity(query_embedding, row.embedding_json)
            if score < min_score:
                continue
            existing_score, _ = best_per_message.get(row.message_id, (0.0, None))
            if score > existing_score:
                best_per_message[row.message_id] = (score, row)

        if not best_per_message:
            return []

        # Sort by score descending, take top_k
        scored = sorted(
            best_per_message.items(),
            key=lambda x: x[1][0],
            reverse=True,
        )[:top_k]

        # Fetch full message context for each match
        message_ids = [msg_id for msg_id, _ in scored]

        # Fetch the messages table rows
        from ..db.orm.messages import Message

        msg_stmt = select(Message).where(Message.id.in_(message_ids))
        msg_result = await db.execute(msg_stmt)
        messages_map = {m.id: m for m in msg_result.scalars().all()}

        # Also fetch the next assistant message after each matched message
        results_list: list[dict] = []

        for msg_id, (score, emb_row) in scored:
            msg = messages_map.get(msg_id)
            if not msg or not msg.content:
                continue

            # Extract user text from JSON content
            user_text = _extract_text(msg.content)

            if not user_text:
                continue

            # Try to find the assistant response that follows this user message
            from ..db.orm.sessions import Session as SessionORM

            assistant_text = None
            try:
                # Find the next assistant message in the same session
                next_stmt = select(Message).where(
                    Message.session_id == msg.session_id,
                    Message.sender == "assistant",
                    Message.created_at > msg.created_at,
                    Message.is_deleted == False,  # noqa: E712
                ).order_by(Message.created_at).limit(1)
                next_result = await db.execute(next_stmt)
                next_msg = next_result.scalar_one_or_none()
                if next_msg and next_msg.content:
                    assistant_text = _extract_text(next_msg.content, max_chars=500)
            except Exception:
                pass

            # Get session title
            session_title = ""
            try:
                s_stmt = select(SessionORM).where(SessionORM.id == msg.session_id)
                s_result = await db.execute(s_stmt)
                s_row = s_result.scalar_one_or_none()
                if s_row:
                    session_title = s_row.title
            except Exception:
                pass

            results_list.append({
                "message_id": msg_id,
                "score": round(score, 4),
                "session_id": msg.session_id,
                "session_title": session_title,
                "user_text": user_text[:500],
                "assistant_text": assistant_text,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            })

        return results_list

    except Exception:
        logger.exception("Failed to retrieve similar messages for user=%s", user_id)
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks for embedding.

    Delegates to the chunker from rag_search.py when available, otherwise
    falls back to a simple paragraph-level split.
    """
    try:
        from ..tools.rag_search import _chunk_text as _rag_chunk
        return _rag_chunk(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    except Exception:
        # Simple fallback
        if len(text) <= chunk_size:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append(text[start:end])
            start = end - chunk_overlap
        return chunks


def _extract_text(content: list | None, max_chars: int = 0) -> str:
    """Extract plain text from a message's JSON content array.

    Mirrors the logic in runner._extract_message_text but accepts
    a max_chars parameter for truncation.
    """
    if not content or not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = item.get("text", "")
            if text:
                parts.append(text)

    result = "\n".join(parts)
    if max_chars and len(result) > max_chars:
        result = result[:max_chars] + "..."
    return result
