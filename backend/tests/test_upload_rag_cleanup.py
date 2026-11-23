# =============================================================================
# PH Agent Hub — Upload deletion RAG cleanup tests (Issue #628)
# =============================================================================
# Regression tests: deleting a session, a message's uploads, or a temp upload
# must remove the RAG chunks indexed from those files, and the chunks must not
# be returned by search_documents() afterwards.
# =============================================================================

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.db.orm.file_uploads import FileUpload
from src.db.orm.messages import Message
from src.db.orm.rag import RAGDocument
from src.services import upload_service
from src.services.rag_service import search_documents
from src.services.session_service import delete_session
from scripts.backfill_orphan_rag_chunks import run as backfill_run

pytestmark = [pytest.mark.integration, pytest.mark.regression]

MARKER_TEXT = "PROJECT-NIGHTFALL-SECRET"


@pytest.fixture(autouse=True)
def no_minio(monkeypatch):
    """Never talk to MinIO from these tests."""
    monkeypatch.setattr(upload_service.s3, "delete_object", AsyncMock())


def _make_upload(
    tenant_id: str,
    user_id: str,
    *,
    session_id: str | None = None,
    message_id: str | None = None,
    temporary: bool = False,
    created_at: datetime | None = None,
    filename: str = "secret.pdf",
) -> FileUpload:
    file_id = str(uuid.uuid4())
    return FileUpload(
        id=file_id,
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        message_id=message_id,
        original_filename=filename,
        content_type="application/pdf",
        size_bytes=128,
        storage_key=f"uploads/{user_id}/{file_id}-{filename}",
        bucket=f"phhub-test-{tenant_id}",
        is_temporary=temporary,
        extracted_text=f"{MARKER_TEXT} chunk body",
        created_at=created_at or datetime.now(timezone.utc),
    )


def _chunk(upload: FileUpload, index: int = 0) -> RAGDocument:
    return RAGDocument(
        id=str(uuid.uuid4()),
        tenant_id=upload.tenant_id,
        file_id=upload.id,
        title=upload.original_filename or "chunk",
        content=f"{MARKER_TEXT} part {index}",
        chunk_index=index,
        extra_metadata={"original_filename": upload.original_filename},
        embedding_json=[0.1, 0.2, 0.3],
        model="test-embedding",
    )


async def _chunk_ids(db: AsyncSession, file_id: str) -> list[str]:
    result = await db.execute(
        select(RAGDocument.id).where(RAGDocument.file_id == file_id)
    )
    return [row[0] for row in result.all()]


async def _count_uploads(db: AsyncSession, file_id: str) -> int:
    result = await db.execute(
        select(func.count()).select_from(FileUpload).where(FileUpload.id == file_id)
    )
    return int(result.scalar() or 0)


async def _search_markers(db: AsyncSession, tenant_id: str) -> list[str]:
    async def _embed(texts, **kwargs):
        return [[0.1, 0.2, 0.3]] * len(texts) if texts else []

    with patch("src.services.rag_service._get_embeddings", side_effect=_embed):
        results = await search_documents(
            db, query="nightfall secret", tenant_id=tenant_id, top_k=10
        )
    return [r["text"] for r in results if MARKER_TEXT in r["text"]]


async def _row_exists(db: AsyncSession, row_id: str) -> bool:
    result = await db.execute(
        select(func.count()).select_from(RAGDocument).where(RAGDocument.id == row_id)
    )
    return int(result.scalar() or 0) > 0


async def test_delete_session_removes_chunks(
    db_session: AsyncSession, test_tenant, test_user, test_session
):
    upload = _make_upload(
        test_tenant.id, test_user.id, session_id=test_session.id
    )
    db_session.add(upload)
    db_session.add(_chunk(upload, 0))
    db_session.add(_chunk(upload, 1))
    await db_session.flush()

    assert len(await _chunk_ids(db_session, upload.id)) == 2
    assert await _search_markers(db_session, test_tenant.id)

    await delete_session(db_session, test_session.id)

    assert await _chunk_ids(db_session, upload.id) == []
    assert await _count_uploads(db_session, upload.id) == 0
    assert await _search_markers(db_session, test_tenant.id) == []


async def test_delete_uploads_for_message_removes_chunks(
    db_session: AsyncSession, test_tenant, test_user, test_session
):
    message = Message(
        id=str(uuid.uuid4()),
        session_id=test_session.id,
        sender="user",
        content=[{"type": "text", "text": "see the attachment"}],
    )
    db_session.add(message)
    # Flush the message first: file_uploads.message_id has an FK to messages,
    # and there is no ORM relationship to order the two inserts.
    await db_session.flush()
    upload = _make_upload(
        test_tenant.id,
        test_user.id,
        session_id=test_session.id,
        message_id=message.id,
    )
    db_session.add(upload)
    db_session.add(_chunk(upload, 0))
    db_session.add(_chunk(upload, 1))
    await db_session.flush()

    assert len(await _chunk_ids(db_session, upload.id)) == 2

    await upload_service.delete_uploads_for_message(db_session, message.id)

    assert await _chunk_ids(db_session, upload.id) == []
    assert await _count_uploads(db_session, upload.id) == 0
    assert await _search_markers(db_session, test_tenant.id) == []


async def test_delete_orphaned_temp_uploads_removes_chunks(
    db_session: AsyncSession, test_tenant, test_user
):
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=settings.TEMPORARY_SESSION_TTL_SECONDS + 7200
    )
    upload = _make_upload(
        test_tenant.id, test_user.id, temporary=True, created_at=stale
    )
    db_session.add(upload)
    db_session.add(_chunk(upload, 0))
    await db_session.flush()

    await upload_service.delete_orphaned_temp_uploads(db_session)

    assert await _chunk_ids(db_session, upload.id) == []
    assert await _count_uploads(db_session, upload.id) == 0


async def test_fk_cascade_removes_chunks(
    db_session: AsyncSession, test_tenant, test_user
):
    """A raw file_uploads delete must take the chunks with it (migration #628)."""
    upload = _make_upload(test_tenant.id, test_user.id)
    db_session.add(upload)
    db_session.add(_chunk(upload, 0))
    await db_session.flush()
    assert len(await _chunk_ids(db_session, upload.id)) == 1

    await db_session.execute(
        sa_delete(FileUpload).where(FileUpload.id == upload.id)
    )
    await db_session.flush()

    assert await _chunk_ids(db_session, upload.id) == []


async def test_backfill_removes_only_upload_orphans(
    db_session: AsyncSession, test_tenant
):
    orphan = RAGDocument(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        file_id=None,
        title="gone.pdf",
        content="orphaned chunk",
        chunk_index=0,
        extra_metadata={"original_filename": "gone.pdf"},
        embedding_json=[0.1, 0.2, 0.3],
        model="test-embedding",
    )
    adhoc = RAGDocument(
        id=str(uuid.uuid4()),
        tenant_id=test_tenant.id,
        file_id=None,
        title="ad-hoc note",
        content="ad-hoc indexed text",
        chunk_index=0,
        extra_metadata={"note": "indexed directly"},
        embedding_json=[0.1, 0.2, 0.3],
        model="test-embedding",
    )
    db_session.add(orphan)
    db_session.add(adhoc)
    await db_session.flush()

    dry = await backfill_run(db_session, apply=False)
    assert dry["orphans"] >= 1
    assert dry["adhoc_retained"] >= 1
    assert dry["deleted"] == 0
    assert await _row_exists(db_session, orphan.id)
    assert await _row_exists(db_session, adhoc.id)

    applied = await backfill_run(db_session, apply=True)
    assert applied["deleted"] >= 1
    assert not await _row_exists(db_session, orphan.id)
    assert await _row_exists(db_session, adhoc.id)