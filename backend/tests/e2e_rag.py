"""E2E test for the RAG pipeline (indexing → search → deletion).

Prerequisites:
- Docker stack is running (docker compose up -d)
- Alembic migrations are up to date

Usage:
    pytest backend/tests/e2e_rag.py -v
"""

import os
import uuid
import warnings
from unittest.mock import patch

import pytest

pytestmark = [
    pytest.mark.e2e,
]

# Ensure the DB URL points to the Docker stack
os.environ.setdefault(
    "DATABASE_URL",
    "mysql+aiomysql://phagent:pRep5v3Nzw_aMMV@mariadb:3306/phagent_hub?charset=utf8mb4",
)


@pytest.mark.e2e
class TestRagE2E:
    """End-to-end RAG pipeline verification."""

    async def test_rag_pipeline(self, e2e_db_session):
        """Verify full RAG flow: create upload → index → search → delete."""
        from src.db.base import AsyncSessionLocal
        from src.db.orm.file_uploads import FileUpload
        from src.db.orm.rag import RAGDocument
        from src.db.orm.users import User
        from src.db.orm.tenants import Tenant
        from src.services.rag_service import index_document, search_documents, delete_document
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            t = (await db.execute(select(Tenant).limit(1))).scalar_one()
            u = (await db.execute(select(User).limit(1))).scalar_one()

            fid = str(uuid.uuid4())
            up = FileUpload(
                id=fid, tenant_id=t.id, user_id=u.id,
                original_filename="e2e.txt", content_type="text/plain",
                size_bytes=100, storage_key=f"t/{fid}.txt", bucket=f"phub-{t.id}",
                extracted_text="Python programming language for AI and data science.",
            )
            db.add(up)
            await db.flush()

            c = await index_document(db, up)
            assert c > 0, "Should index at least one chunk"

            rows = (await db.execute(
                select(RAGDocument).where(RAGDocument.file_id == fid)
            )).scalars().all()
            assert len(rows) > 0, "RAGDocument rows should exist"
            assert all(r.embedding_json for r in rows), "All chunks should have embeddings"

            async def me(t, **kw):
                return [[0.1] * 256 for _ in t]

            with patch("src.services.rag_service._get_embeddings", me):
                r = await search_documents(db, "Python", tenant_id=t.id)
            assert len(r) > 0, "Search should return results"

            d = await delete_document(db, fid)
            assert d > 0, "Should delete chunks"

            rem = (await db.execute(
                select(RAGDocument).where(RAGDocument.file_id == fid)
            )).scalars().all()
            assert len(rem) == 0, "All chunks should be deleted"

            await db.rollback()
