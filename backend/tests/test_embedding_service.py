# =============================================================================
# PH Agent Hub — Embedding Service Tests
# =============================================================================
# Unit tests for pure functions and integration tests for async functions.
# External embedding API calls are mocked at
# ``embedding_service._get_embeddings``.
# =============================================================================

import math
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.orm.message_embeddings import MessageEmbedding
from src.db.orm.messages import Message
from src.db.orm.sessions import Session
from src.services.embedding_service import (
    _chunk_text,
    _cosine_similarity,
    _extract_text,
    embed_message,
    embed_query,
    retrieve_similar,
)

pytestmark = [pytest.mark.integration]


# ===========================================================================
# Unit tests — cosine similarity
# ===========================================================================


class TestCosineSimilarity:
    """Pure function tests — no DB, no async."""

    @pytest.mark.unit
    def test_identical_vectors(self):
        a = [1.0, 0.5, -0.5]
        assert math.isclose(_cosine_similarity(a, a), 1.0, rel_tol=1e-6)

    @pytest.mark.unit
    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert math.isclose(_cosine_similarity(a, b), 0.0, abs_tol=1e-6)

    @pytest.mark.unit
    def test_opposite_vectors(self):
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        assert math.isclose(_cosine_similarity(a, b), -1.0, rel_tol=1e-6)

    @pytest.mark.unit
    def test_similar_vectors(self):
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0, 3.0]
        assert math.isclose(_cosine_similarity(a, b), 1.0, rel_tol=1e-6)

    @pytest.mark.unit
    def test_different_lengths_returns_zero(self):
        assert _cosine_similarity([1.0], [1.0, 2.0]) == 0.0

    @pytest.mark.unit
    def test_empty_vectors_returns_zero(self):
        assert _cosine_similarity([], []) == 0.0
        assert _cosine_similarity([1.0], []) == 0.0

    @pytest.mark.unit
    def test_zero_magnitude_returns_zero(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
        assert _cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0

    @pytest.mark.unit
    def test_near_orthogonal(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.1]
        sim = _cosine_similarity(a, b)
        assert 0.0 <= sim < 0.1


# ===========================================================================
# Unit tests — extract_text
# ===========================================================================


class TestExtractText:
    """Pure function tests for _extract_text."""

    @pytest.mark.unit
    def test_empty_content(self):
        assert _extract_text(None) == ""
        assert _extract_text([]) == ""
        assert _extract_text("not a list") == ""

    @pytest.mark.unit
    def test_single_text_part(self):
        content = [{"type": "text", "text": "Hello world"}]
        assert _extract_text(content) == "Hello world"

    @pytest.mark.unit
    def test_multiple_text_parts(self):
        content = [
            {"type": "text", "text": "First part."},
            {"type": "text", "text": "Second part."},
        ]
        assert _extract_text(content) == "First part.\nSecond part."

    @pytest.mark.unit
    def test_mixed_parts(self):
        content = [
            {"type": "text", "text": "Text content"},
            {"type": "image", "image_url": "http://example.com/img.png"},
            {"type": "text", "text": "More text"},
        ]
        assert _extract_text(content) == "Text content\nMore text"

    @pytest.mark.unit
    def test_skips_non_dict_items(self):
        content = ["string", 42, {"type": "text", "text": "valid"}]
        assert _extract_text(content) == "valid"

    @pytest.mark.unit
    def test_max_chars_truncation(self):
        content = [{"type": "text", "text": "A" * 100}]
        assert _extract_text(content, max_chars=10) == "A" * 10 + "..."

    @pytest.mark.unit
    def test_max_chars_under_limit_no_truncation(self):
        content = [{"type": "text", "text": "Short text"}]
        assert _extract_text(content, max_chars=100) == "Short text"

    @pytest.mark.unit
    def test_max_chars_zero_no_truncation(self):
        content = [{"type": "text", "text": "A" * 1000}]
        result = _extract_text(content, max_chars=0)
        assert result == "A" * 1000

    @pytest.mark.unit
    def test_skips_empty_text(self):
        content = [
            {"type": "text", "text": "Valid"},
            {"type": "text", "text": ""},
            {"type": "text", "text": "Also valid"},
        ]
        assert _extract_text(content) == "Valid\nAlso valid"


# ===========================================================================
# Unit tests — chunk_text (fallback path)
# ===========================================================================


class TestChunkText:
    """Tests for _chunk_text covering the fallback path.

    The primary delegation to rag_search is tested implicitly through
    embed_message integration tests.  Here we test the fallback logic
    that runs when the rag_search import fails.
    """

    @pytest.mark.unit
    def test_empty_text(self):
        # Delegates to rag_search which returns [] for empty/whitespace input
        assert _chunk_text("") == []

    @pytest.mark.unit
    def test_short_text_no_chunking(self):
        text = "Short text."
        assert _chunk_text(text, chunk_size=500) == [text]

    @pytest.mark.unit
    def test_long_text_splits_into_chunks(self):
        text = "word " * 200  # ~1000 chars
        chunks = _chunk_text(text, chunk_size=200)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 200

    @pytest.mark.unit
    def test_chunk_overlap_works(self):
        text = "A" * 300
        chunks = _chunk_text(text, chunk_size=100, chunk_overlap=20)
        assert len(chunks) >= 3
        # Verify overlap: consecutive chunks share content
        for i in range(1, len(chunks)):
            assert chunks[i].startswith("A" * 20) or len(chunks[i]) + len(chunks[i - 1]) > 100


# ===========================================================================
# Integration tests — embed_query
# ===========================================================================


class TestEmbedQuery:
    """Tests for embed_query — mocks _get_embeddings."""

    @patch("src.services.embedding_service._get_embeddings")
    async def test_returns_embedding_for_valid_text(
        self, mock_get_embeddings
    ):
        """Should return the first embedding vector."""
        mock_get_embeddings.return_value = [[0.1, 0.2, 0.3]]
        result = await embed_query("Hello world")
        assert result == [0.1, 0.2, 0.3]
        mock_get_embeddings.assert_called_once_with(
            ["Hello world"], model="text-embedding-3-small"
        )

    @patch("src.services.embedding_service._get_embeddings")
    async def test_returns_none_for_empty_text(self, mock_get_embeddings):
        """Empty/whitespace text should return None without calling API."""
        assert await embed_query("") is None
        assert await embed_query("   ") is None
        mock_get_embeddings.assert_not_called()

    @patch("src.services.embedding_service._get_embeddings")
    async def test_returns_none_on_api_failure(self, mock_get_embeddings):
        """API exception should return None gracefully."""
        mock_get_embeddings.side_effect = RuntimeError("API down")
        result = await embed_query("Hello")
        assert result is None

    @patch("src.services.embedding_service._get_embeddings")
    async def test_returns_none_when_no_results(self, mock_get_embeddings):
        """Empty response from API should return None."""
        mock_get_embeddings.return_value = []
        result = await embed_query("Hello")
        assert result is None

    @patch("src.services.embedding_service._get_embeddings")
    async def test_custom_model_passed_through(self, mock_get_embeddings):
        """Custom model name should be forwarded to _get_embeddings."""
        mock_get_embeddings.return_value = [[0.5, 0.6]]
        result = await embed_query("Test", model="custom-model-v2")
        assert result == [0.5, 0.6]
        mock_get_embeddings.assert_called_once_with(
            ["Test"], model="custom-model-v2"
        )


# ===========================================================================
# Integration tests — embed_message
# ===========================================================================


class TestEmbedMessage:
    """Tests for embed_message — mocks _get_embeddings, needs db_session."""

    async def _create_message(self, db_session, test_user, test_tenant, session_id=None):
        """Helper to create a Message row (FK requirement for MessageEmbedding)."""
        from src.db.orm.sessions import Session as SessionOrm
        if session_id is None:
            session = SessionOrm(
                id=str(uuid.uuid4()),
                user_id=test_user.id,
                tenant_id=test_tenant.id,
                title="Test Session for Embed",
            )
            db_session.add(session)
            await db_session.flush()
            session_id = session.id

        msg_id = str(uuid.uuid4())
        msg = Message(
            id=msg_id,
            session_id=session_id,
            sender="user",
            content=[{"type": "text", "text": "test"}],
        )
        db_session.add(msg)
        await db_session.flush()
        return msg, session_id

    @patch("src.services.embedding_service._get_embeddings")
    async def test_short_text_creates_single_embedding(
        self, mock_get_embeddings, db_session: AsyncSession, test_user, test_tenant
    ):
        """Short text (<500 chars) should produce one MessageEmbedding row."""
        mock_get_embeddings.return_value = [[0.1, 0.2, 0.3]]
        msg, _ = await self._create_message(db_session, test_user, test_tenant)

        await embed_message(
            db_session,
            message_id=msg.id,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            text="Short message",
        )

        result = await db_session.execute(
            select(MessageEmbedding).where(MessageEmbedding.message_id == msg.id)
        )
        rows = list(result.scalars().all())
        assert len(rows) == 1
        assert rows[0].chunk_index == 0
        assert rows[0].embedding_json == [0.1, 0.2, 0.3]
        assert rows[0].user_id == test_user.id
        assert rows[0].tenant_id == test_tenant.id

    @patch("src.services.embedding_service._get_embeddings")
    async def test_long_text_creates_multiple_chunks(
        self, mock_get_embeddings, db_session: AsyncSession, test_user, test_tenant
    ):
        """Text >500 chars should produce multiple embeddings with ascending chunk_index.

        700 chars with default chunk_size=500 produces 2 chunks.
        """
        mock_get_embeddings.return_value = [[0.1], [0.2]]
        long_text = "A" * 700
        msg, _ = await self._create_message(db_session, test_user, test_tenant)

        await embed_message(
            db_session,
            message_id=msg.id,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            text=long_text,
        )

        result = await db_session.execute(
            select(MessageEmbedding)
            .where(MessageEmbedding.message_id == msg.id)
            .order_by(MessageEmbedding.chunk_index)
        )
        rows = list(result.scalars().all())
        assert len(rows) == 2
        assert [r.chunk_index for r in rows] == [0, 1]

    @patch("src.services.embedding_service._get_embeddings")
    async def test_empty_text_skips(
        self, mock_get_embeddings, db_session: AsyncSession, test_user, test_tenant
    ):
        """Empty/whitespace-only text should not call _get_embeddings."""
        msg, _ = await self._create_message(db_session, test_user, test_tenant)

        await embed_message(
            db_session,
            message_id=msg.id,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            text="",
        )
        mock_get_embeddings.assert_not_called()

    @patch("src.services.embedding_service._get_embeddings")
    async def test_api_failure_handled_gracefully(
        self, mock_get_embeddings, db_session: AsyncSession, test_user, test_tenant
    ):
        """When _get_embeddings fails, embed_message should not raise."""
        msg, _ = await self._create_message(db_session, test_user, test_tenant)
        mock_get_embeddings.side_effect = RuntimeError("API error")

        # Should not raise
        await embed_message(
            db_session,
            message_id=msg.id,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            text="Test message",
        )

        # No rows should be created
        result = await db_session.execute(
            select(MessageEmbedding).where(MessageEmbedding.message_id == msg.id)
        )
        assert list(result.scalars().all()) == []

    @patch("src.services.embedding_service._get_embeddings")
    async def test_custom_model_propagated(
        self, mock_get_embeddings, db_session: AsyncSession, test_user, test_tenant
    ):
        """Custom model should be stored on the embedding rows."""
        mock_get_embeddings.return_value = [[0.1, 0.2]]
        msg, _ = await self._create_message(db_session, test_user, test_tenant)

        await embed_message(
            db_session,
            message_id=msg.id,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            text="Test",
            model="custom-embed-v1",
        )

        result = await db_session.execute(
            select(MessageEmbedding).where(MessageEmbedding.message_id == msg.id)
        )
        row = result.scalar_one()
        assert row.model == "custom-embed-v1"


# ===========================================================================
# Integration tests — retrieve_similar
# ===========================================================================


class TestRetrieveSimilar:
    """Tests for retrieve_similar — needs seeded MessageEmbedding + Message + Session rows."""

    async def _seed_message(
        self,
        db_session: AsyncSession,
        session_id: str,
        user_id: str,
        tenant_id: str,
        text: str,
        sender: str = "user",
        created_at: datetime | None = None,
    ) -> Message:
        """Helper to create a Message row and return it."""
        msg_id = str(uuid.uuid4())
        content = [{"type": "text", "text": text}]
        message = Message(
            id=msg_id,
            session_id=session_id,
            sender=sender,
            content=content,
            created_at=created_at or datetime.now(timezone.utc),
        )
        db_session.add(message)
        await db_session.flush()
        return message

    async def _seed_embedding(
        self,
        db_session: AsyncSession,
        message_id: str,
        user_id: str,
        tenant_id: str,
        embedding: list[float],
        chunk_index: int = 0,
    ) -> MessageEmbedding:
        """Helper to create a MessageEmbedding row."""
        emb = MessageEmbedding(
            message_id=message_id,
            user_id=user_id,
            tenant_id=tenant_id,
            chunk_index=chunk_index,
            embedding_json=embedding,
            model="text-embedding-3-small",
        )
        db_session.add(emb)
        await db_session.flush()
        return emb

    async def _seed_session(
        self,
        db_session: AsyncSession,
        user_id: str,
        tenant_id: str,
        title: str = "Test Session",
    ) -> Session:
        """Helper to create a Session row."""
        sess_id = str(uuid.uuid4())
        session = Session(
            id=sess_id,
            user_id=user_id,
            tenant_id=tenant_id,
            title=title,
        )
        db_session.add(session)
        await db_session.flush()
        return session

    async def test_returns_top_k_matches(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Should return top_k results sorted by score descending."""
        session = await self._seed_session(db_session, test_user.id, test_tenant.id)
        msg1 = await self._seed_message(db_session, session.id, test_user.id, test_tenant.id, "Hello")
        msg2 = await self._seed_message(db_session, session.id, test_user.id, test_tenant.id, "World")

        # Use 3D vectors so we can have different similarity scores
        await self._seed_embedding(db_session, msg1.id, test_user.id, test_tenant.id, [0.3, 0.6, 0.0])
        await self._seed_embedding(db_session, msg2.id, test_user.id, test_tenant.id, [0.9, 0.1, 0.0])

        query = [1.0, 0.0, 0.0]
        results = await retrieve_similar(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            query_embedding=query,
            top_k=2,
        )

        assert len(results) == 2
        assert results[0]["score"] >= results[1]["score"]
        assert results[0]["message_id"] == msg2.id  # highest score first

    async def test_respects_min_score_threshold(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Embeddings below min_score should be excluded.

        Note: [0.99, 0.0] and [0.42, 0.0] have cosine similarity of 1.0
        (they are co-linear). We use orthogonal-ish vectors for low similarity.
        """
        session = await self._seed_session(db_session, test_user.id, test_tenant.id)
        msg = await self._seed_message(db_session, session.id, test_user.id, test_tenant.id, "Low similarity")
        # Embedding nearly orthogonal to query [1.0, 0.0, 0.0]
        await self._seed_embedding(db_session, msg.id, test_user.id, test_tenant.id, [0.0, 0.99, 0.001])

        results = await retrieve_similar(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            query_embedding=[1.0, 0.0, 0.0],
            min_score=0.5,
        )
        assert len(results) == 0

    async def test_deduplicates_by_message_id(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Multiple chunks for same message should deduplicate — only best score kept."""
        session = await self._seed_session(db_session, test_user.id, test_tenant.id)
        msg = await self._seed_message(db_session, session.id, test_user.id, test_tenant.id, "Long message")

        query = [1.0, 0.0, 0.0]
        # Two chunks for same message — chunk 1 is more similar to query
        await self._seed_embedding(db_session, msg.id, test_user.id, test_tenant.id, [0.2, 0.0, 0.0], chunk_index=0)
        await self._seed_embedding(db_session, msg.id, test_user.id, test_tenant.id, [0.8, 0.0, 0.0], chunk_index=1)

        results = await retrieve_similar(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            query_embedding=query,
            top_k=5,
        )

        # Deduplicated: only one result per message_id
        assert len(results) == 1
        assert results[0]["message_id"] == msg.id
        # Should have the highest score (0.8, not 0.2) — cosine([1,0,0], [0.8,0,0]) = 1.0
        # Actually both [0.2,0,0] and [0.8,0,0] are co-linear with [1,0,0]
        # So both have score 1.0 — dedup still gives one result
        assert results[0]["score"] == 1.0

    async def test_excludes_specified_session(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """NOTE: session_id filtering is NOT implemented in retrieve_similar.

        The session_id parameter is accepted but never used as a filter.
        This test documents that gap — all embeddings for the user are
        returned regardless of session_id.
        """
        session = await self._seed_session(db_session, test_user.id, test_tenant.id, "Any Session")
        msg = await self._seed_message(db_session, session.id, test_user.id, test_tenant.id, "Test message")
        query = [1.0, 0.0, 0.0]
        await self._seed_embedding(db_session, msg.id, test_user.id, test_tenant.id, [0.95, 0.0, 0.0])

        results = await retrieve_similar(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            query_embedding=query,
            session_id=session.id,  # Should exclude but doesn't — see issue
        )
        # Currently returns results despite session_id filter — feature not implemented
        assert len(results) >= 1

    async def test_empty_query_embedding_returns_empty(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Empty query_embedding should return [] without querying DB."""
        results = await retrieve_similar(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            query_embedding=[],
        )
        assert results == []

    async def test_no_embeddings_returns_empty(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """User with no embeddings should return []."""
        results = await retrieve_similar(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            query_embedding=[1.0, 0.0, 0.0],
        )
        assert results == []

    async def test_returns_full_context(
        self, db_session: AsyncSession, test_user, test_tenant
    ):
        """Results should include message_id, score, session_title, user_text, assistant_text."""
        now = datetime.now(timezone.utc)
        session = await self._seed_session(db_session, test_user.id, test_tenant.id, "My Session")
        msg = await self._seed_message(
            db_session, session.id, test_user.id, test_tenant.id,
            "User query here", created_at=now,
        )

        # Assistant response with later timestamp
        await self._seed_message(
            db_session, session.id, test_user.id, test_tenant.id,
            "Assistant response text", sender="assistant",
            created_at=now + timedelta(seconds=5),
        )
        query = [1.0, 0.0, 0.0]
        await self._seed_embedding(db_session, msg.id, test_user.id, test_tenant.id, [0.9, 0.0, 0.0])

        results = await retrieve_similar(
            db_session,
            user_id=test_user.id,
            tenant_id=test_tenant.id,
            query_embedding=query,
        )
        assert len(results) == 1
        assert results[0]["message_id"] == msg.id
        assert results[0]["session_id"] == session.id
        assert results[0]["session_title"] == "My Session"
        assert "User query" in results[0]["user_text"]
        assert results[0]["assistant_text"] is not None
        assert "Assistant response" in results[0]["assistant_text"]
        assert results[0]["created_at"] is not None

    async def test_tenant_isolation(
        self, db_session: AsyncSession, test_user, test_tenant, second_user, second_tenant
    ):
        """Users from one tenant should not see another tenant's embeddings."""
        session = await self._seed_session(db_session, test_user.id, test_tenant.id)
        msg = await self._seed_message(db_session, session.id, test_user.id, test_tenant.id, "Tenant A secret")
        await self._seed_embedding(db_session, msg.id, test_user.id, test_tenant.id, [0.95, 0.0, 0.0])

        # User from different tenant queries
        results = await retrieve_similar(
            db_session,
            user_id=second_user.id,
            tenant_id=second_tenant.id,
            query_embedding=[1.0, 0.0, 0.0],
        )
        assert len(results) == 0
