# =============================================================================
# PH Agent Hub — RAG Service Tests
# =============================================================================
# Unit and integration tests for the RAG document indexing and search pipeline.
# =============================================================================

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.orm.rag import RAGDocument
from services.rag_service import (
    _chunk_text,
    _cosine_similarity,
    index_document,
    delete_document,
    search_documents,
    get_document_count,
    list_documents,
)


# ===========================================================================
# Unit tests — chunking
# ===========================================================================


class TestChunkText:
    def test_empty_text(self):
        assert _chunk_text("") == []
        assert _chunk_text("   ") == []
        assert _chunk_text(None) == []

    def test_short_text_no_chunking(self):
        text = "Short text."
        chunks = _chunk_text(text, chunk_size=500)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_chunk_paragraph_boundary(self):
        """Should split on paragraphs when possible."""
        text = "A" * 300 + "\n\n" + "B" * 300
        chunks = _chunk_text(text, chunk_size=400)
        assert len(chunks) >= 2

    def test_large_text_produces_multiple_chunks(self):
        text = "word " * 2000  # ~10,000 chars
        chunks = _chunk_text(text, chunk_size=500)
        assert len(chunks) > 1
        # Each chunk should be at most chunk_size
        for c in chunks:
            assert len(c) <= 500


# ===========================================================================
# Unit tests — cosine similarity
# ===========================================================================


class TestCosineSimilarity:
    def test_identical_vectors(self):
        a = [1.0, 0.0, 0.0]
        assert _cosine_similarity(a, a) == 1.0

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_similar_vectors(self):
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0, 3.0]
        assert math.isclose(_cosine_similarity(a, b), 1.0, rel_tol=1e-6)

    def test_different_lengths(self):
        assert _cosine_similarity([1.0], [1.0, 2.0]) == 0.0

    def test_empty_vectors(self):
        assert _cosine_similarity([], []) == 0.0

    def test_near_orthogonal(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.1]
        sim = _cosine_similarity(a, b)
        assert 0.0 <= sim < 0.1


# ===========================================================================
# Integration tests — index_document
# ===========================================================================


class TestIndexDocument:
    async def _mock_embeddings(self, texts, **kwargs):
        """Return fake embeddings for testing."""
        return [[0.1, 0.2, 0.3]] * len(texts) if texts else []

    @patch("services.rag_service._get_embeddings")
    async def test_index_creates_chunks(
        self, mock_embed, db_session: AsyncSession, sample_file_upload
    ):
        mock_embed.side_effect = self._mock_embeddings

        count = await index_document(db_session, sample_file_upload)
        assert count > 0

        # Verify rows in DB
        result = await db_session.execute(
            select(RAGDocument).where(
                RAGDocument.file_id == sample_file_upload.id
            )
        )
        rows = list(result.scalars().all())
        assert len(rows) == count
        assert all(r.file_id == sample_file_upload.id for r in rows)
        assert all(r.embedding_json is not None for r in rows)

    @patch("services.rag_service._get_embeddings")
    async def test_index_stores_embeddings(
        self, mock_embed, db_session: AsyncSession, sample_file_upload
    ):
        mock_embed.side_effect = self._mock_embeddings

        await index_document(db_session, sample_file_upload)

        result = await db_session.execute(
            select(RAGDocument).where(
                RAGDocument.file_id == sample_file_upload.id
            )
        )
        rows = list(result.scalars().all())
        for r in rows:
            assert r.embedding_json == [0.1, 0.2, 0.3]
            assert r.model is not None

    @patch("services.rag_service._get_embeddings")
    async def test_index_is_idempotent(
        self, mock_embed, db_session: AsyncSession, sample_file_upload
    ):
        mock_embed.side_effect = self._mock_embeddings

        count1 = await index_document(db_session, sample_file_upload)
        count2 = await index_document(db_session, sample_file_upload)

        # Second call should replace old chunks, not duplicate
        assert count1 == count2

        result = await db_session.execute(
            select(RAGDocument).where(
                RAGDocument.file_id == sample_file_upload.id
            )
        )
        rows = list(result.scalars().all())
        assert len(rows) == count1  # Same count after re-index

    async def test_index_empty_text(self, db_session: AsyncSession, sample_file_upload):
        sample_file_upload.extracted_text = ""
        with patch("services.rag_service._get_embeddings") as mock_embed:
            mock_embed.side_effect = self._mock_embeddings
            count = await index_document(db_session, sample_file_upload)
            assert count == 0


# ===========================================================================
# Integration tests — search_documents
# ===========================================================================


class TestSearchDocuments:
    @patch("services.rag_service._get_embeddings")
    async def test_search_returns_relevant_chunks(
        self, mock_embed, db_session: AsyncSession, sample_file_upload
    ):
        # First, index the document
        async def _embed(texts, **kwargs):
            return [[0.1, 0.2, 0.3]] * len(texts) if texts else []

        mock_embed.side_effect = _embed

        await index_document(db_session, sample_file_upload)

        # Now search — mock the query embedding separately
        async def _query_embed(texts, **kwargs):
            # Return an embedding that will have high similarity
            return [[0.1, 0.2, 0.3]] if texts else []

        with patch("services.rag_service._get_embeddings") as query_mock:
            query_mock.side_effect = _query_embed

            results = await search_documents(
                db_session,
                query="tell me about Python",
                tenant_id=sample_file_upload.tenant_id,
                top_k=5,
            )

        assert len(results) > 0
        assert all("text" in r for r in results)
        assert all("score" in r for r in results)
        assert all("file_id" in r for r in results)
        assert results[0]["score"] > 0

    @patch("services.rag_service._get_embeddings")
    async def test_search_respects_tenant_isolation(
        self, mock_embed, db_session: AsyncSession, test_tenant, test_user
    ):
        async def _embed(texts, **kwargs):
            return [[0.1, 0.2, 0.3]] * len(texts) if texts else []

        mock_embed.side_effect = _embed

        # Create a file in tenant A
        from db.orm.file_uploads import FileUpload

        upload_a = FileUpload(
            id="test-upload-a",
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            original_filename="doc_a.txt",
            content_type="text/plain",
            size_bytes=100,
            storage_key="test/a.txt",
            bucket=f"phhub-test-{test_tenant.id}",
            extracted_text="Python programming language",
        )
        db_session.add(upload_a)
        await db_session.flush()

        await index_document(db_session, upload_a)

        # Search as tenant A — should find results
        async def _query_embed(texts, **kwargs):
            return [[0.1, 0.2, 0.3]] if texts else []

        with patch("services.rag_service._get_embeddings") as query_mock:
            query_mock.side_effect = _query_embed
            results_a = await search_documents(
                db_session,
                query="Python",
                tenant_id=test_tenant.id,
                top_k=5,
            )

        assert len(results_a) > 0

        # Search as a DIFFERENT tenant — should find nothing
        other_tenant_id = "other-tenant-id"
        with patch("services.rag_service._get_embeddings") as query_mock2:
            query_mock2.side_effect = _query_embed
            results_b = await search_documents(
                db_session,
                query="Python",
                tenant_id=other_tenant_id,
                top_k=5,
            )

        assert len(results_b) == 0

    @patch("services.rag_service._get_embeddings")
    async def test_search_empty_query(
        self, mock_embed, db_session: AsyncSession, sample_file_upload
    ):
        results = await search_documents(
            db_session,
            query="",
            tenant_id=sample_file_upload.tenant_id,
        )
        assert results == []

    @patch("services.rag_service._get_embeddings")
    async def test_search_no_documents(
        self, mock_embed, db_session: AsyncSession, test_tenant
    ):
        async def _embed(texts, **kwargs):
            return [[0.1, 0.2, 0.3]] if texts else []

        mock_embed.side_effect = _embed
        results = await search_documents(
            db_session,
            query="anything",
            tenant_id=test_tenant.id,
        )
        assert results == []


# ===========================================================================
# Integration tests — delete_document
# ===========================================================================


class TestDeleteDocument:
    @patch("services.rag_service._get_embeddings")
    async def test_delete_removes_chunks(
        self, mock_embed, db_session: AsyncSession, sample_file_upload
    ):
        async def _embed(texts, **kwargs):
            return [[0.1, 0.2, 0.3]] * len(texts) if texts else []

        mock_embed.side_effect = _embed

        count = await index_document(db_session, sample_file_upload)
        assert count > 0

        # Delete
        deleted = await delete_document(db_session, sample_file_upload.id)
        assert deleted == count

        # Verify no rows remain
        result = await db_session.execute(
            select(RAGDocument).where(
                RAGDocument.file_id == sample_file_upload.id
            )
        )
        rows = list(result.scalars().all())
        assert len(rows) == 0

    async def test_delete_nonexistent(self, db_session: AsyncSession):
        deleted = await delete_document(db_session, "nonexistent-file-id")
        assert deleted == 0


# ===========================================================================
# Integration tests — list_documents
# ===========================================================================


class TestListDocuments:
    @patch("services.rag_service._get_embeddings")
    async def test_list_returns_documents_grouped(
        self, mock_embed, db_session: AsyncSession, sample_file_upload
    ):
        async def _embed(texts, **kwargs):
            return [[0.1, 0.2, 0.3]] * len(texts) if texts else []

        mock_embed.side_effect = _embed

        await index_document(db_session, sample_file_upload)

        items, total = await list_documents(
            db_session,
            tenant_id=sample_file_upload.tenant_id,
        )

        assert total >= 1
        assert any(item["file_id"] == sample_file_upload.id for item in items)

    async def test_list_empty(self, db_session: AsyncSession, test_tenant):
        items, total = await list_documents(
            db_session,
            tenant_id=test_tenant.id,
        )
        assert total == 0
        assert items == []
