# =============================================================================
# PH Agent Hub — Upload Service
# =============================================================================
# File-upload lifecycle (MinIO + ``file_uploads`` table).
# Only ``storage/s3.py`` calls ``boto3`` (single-module rule).
# =============================================================================

import asyncio
import logging
import mimetypes
import os
import re
import tempfile
import unicodedata
import uuid
from urllib.parse import quote

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from ..core.config import settings
from ..core.exceptions import ForbiddenError, NotFoundError, ValidationError
from ..db.orm.file_uploads import FileUpload
from ..db.orm.users import User
from ..storage import s3

# Document MIME types that can have text extracted
_EXTRACTABLE_MIME_TYPES = frozenset({
    "application/pdf",
    "text/csv",
    "text/plain",
    "text/markdown",
    "application/json",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
})

# Image MIME types (no text extraction)
_IMAGE_MIME_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
})

# Generic / unknown MIME types reported by browsers that should trigger
# extension-based fallback detection
_GENERIC_MIME_TYPES = frozenset({
    "application/octet-stream",
    "application/x-octet-stream",
    "binary/octet-stream",
})


# ---------------------------------------------------------------------------
# MIME type resolution with extension fallback
# ---------------------------------------------------------------------------

# Extended mapping for common Office file extensions that Python's
# mimetypes module may not know about or may report differently than
# the IANA / official MIME types used in UPLOAD_ALLOWED_TYPES.
_EXTENSION_MIME_OVERRIDES: dict[str, str] = {
    # Office documents
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    # Plain text / data
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".log": "text/plain",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".conf": "text/plain",
    ".env": "text/plain",
    ".toml": "text/plain",
    # Config / markup / stylesheet
    ".yml": "text/plain",
    ".yaml": "text/plain",
    ".xml": "text/plain",
    ".html": "text/plain",
    ".htm": "text/plain",
    ".css": "text/plain",
    # Scripting / programming
    ".js": "text/plain",
    ".jsx": "text/plain",
    ".ts": "text/plain",
    ".tsx": "text/plain",
    ".py": "text/plain",
    ".rb": "text/plain",
    ".go": "text/plain",
    ".rs": "text/plain",
    ".java": "text/plain",
    ".sh": "text/plain",
    ".bash": "text/plain",
    ".zsh": "text/plain",
    ".bat": "text/plain",
    ".ps1": "text/plain",
    ".sql": "text/plain",
    ".r": "text/plain",
    # Web / template
    ".vue": "text/plain",
    ".svelte": "text/plain",
    ".php": "text/plain",
    # Documentation / diff
    ".tex": "text/plain",
    ".rst": "text/plain",
    ".diff": "text/plain",
    ".patch": "text/plain",
    # Build / infra
    ".dockerfile": "text/plain",
    ".makefile": "text/plain",
    ".gradle": "text/plain",
    ".tf": "text/plain",  # Terraform
    ".json5": "text/plain",
    # Images
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def _resolve_content_type(content_type: str, filename: str) -> str:
    """Resolve the effective MIME type for a file.

    When the browser reports a generic / opaque content type (e.g.
    ``application/octet-stream``), this function falls back to
    extension-based detection so that Word, Excel, and other Office
    files are still accepted.

    Returns the original *content_type* unchanged when it is already
    specific enough, or the detected type when a fallback is needed.
    """
    if content_type and content_type not in _GENERIC_MIME_TYPES:
        return content_type

    # Try extension-based detection
    _, ext = os.path.splitext(filename)
    ext = ext.lower()

    # Check override mapping first (handles Office formats that
    # mimetypes may not know about)
    if ext in _EXTENSION_MIME_OVERRIDES:
        return _EXTENSION_MIME_OVERRIDES[ext]

    # Fall back to Python's mimetypes module
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed

    # Nothing worked — return the original (the caller may apply its
    # own text/plain fallback for generic browser-reported types;
    # see Issue #473).
    return content_type


# ---------------------------------------------------------------------------
# Filename sanitization utilities
# ---------------------------------------------------------------------------


def _sanitize_storage_filename(original_filename: str) -> str:
    """Normalize a filename for safe use in S3 storage keys.

    * Normalizes Unicode (NFKD) and encodes to ASCII, replacing non-ASCII
      characters with underscores.
    * Removes path separators (``/``, ``\\``), null bytes, and characters
      unsafe for HTTP headers (``"'\;:*?<>|``).
    * Collapses consecutive underscores and strips leading/trailing dots,
      spaces, and underscores.
    * Truncates to 200 characters, preserving the file extension.
    * Falls back to ``"file"`` if the result is empty.

    This function is *deterministic* \u2014 the same input always produces
    the same output.
    """
    # 1. Normalize Unicode (NFKD) and encode to ASCII, replacing non-ASCII
    name = unicodedata.normalize("NFKD", original_filename)
    name = name.encode("ascii", "replace").decode("ascii")

    # 2. Replace path separators and null bytes
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "_")

    # 2a. Collapse consecutive dots (``..`` / ``...`` directory traversal)
    name = re.sub(r"\.{2,}", "_", name)

    # 3. Remove characters unsafe for S3 keys and HTTP headers
    name = re.sub(r"[\"\';:*?<>|]", "_", name)

    # 3a. Replace spaces with underscores (safe for S3 keys and URLs)
    name = name.replace(" ", "_")

    # 4. Collapse consecutive underscores
    name = re.sub(r"_+", "_", name)

    # 5. Strip leading/trailing underscores and spaces (keep dots — they
    #    may be part of the file extension like ``.docx``)
    name = name.strip(" _")

    # 5a. Strip trailing dots only (e.g. ``"file."`` → ``"file"``)
    name = name.rstrip(".")

    # 6. Fallback
    if not name:
        return "file"

    # 7. Truncate to 200 chars, preserving extension when possible
    if len(name) > 200:
        base, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 20:
            name = base[: 200 - len(ext) - 1] + dot + ext
        else:
            name = name[:200]

    return name


def _encode_content_disposition_filename(original_filename: str) -> str:
    """Build a ``Content-Disposition`` header with RFC\u00a05987 encoding.

    For ASCII-only filenames, uses the simple ``filename="..."`` form.
    For filenames with non-ASCII characters, emits **both**:

    * ``filename`` \u2014 ASCII-sanitized fallback
    * ``filename*`` \u2014 RFC\u00a05987 ``UTF-8''percent-encoded`` value

    Examples:
        ``"simple.pdf"``
        \u2192 ``attachment; filename="simple.pdf"``

        ``"r\u00e9sum\u00e9.pdf"``
        \u2192 ``attachment; filename="resume.pdf"; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf``
    """
    safe_ascii = _sanitize_storage_filename(original_filename)

    # Check if non-ASCII chars exist in original
    has_non_ascii = any(ord(c) > 127 for c in original_filename)

    if not has_non_ascii:
        return f'attachment; filename="{safe_ascii}"'

    # RFC 5987: filename*=UTF-8''url-encoded-value
    encoded = quote(original_filename, safe="")
    return f'attachment; filename="{safe_ascii}"; filename*=UTF-8\'\'{encoded}'


async def create_upload(
    db: AsyncSession,
    session_data: dict,
    current_user: User | None,
    file_bytes: bytes,
    original_filename: str,
    content_type: str,
) -> FileUpload:
    """Upload a file to MinIO and create a ``FileUpload`` DB row.

    Raises:
        ValidationError: content_type not allowed or file too large.
        ForbiddenError: session is temporary.
    """
    # 0. Resolve effective content type (fallback from extension when
    #    the browser reports a generic type like application/octet-stream)
    resolved_type = _resolve_content_type(content_type, original_filename)

    # 1. Validate content type
    allowed_types = [
        t.strip() for t in settings.UPLOAD_ALLOWED_TYPES.split(",") if t.strip()
    ]
    if resolved_type not in allowed_types:
        # 1a. Final safety net (Issue #473): if the browser reported a
        #     generic / opaque type (e.g. application/octet-stream) and
        #     the resolved type is still not in the allowlist, fall back
        #     to text/plain.  This lets users upload files with unknown
        #     extensions (e.g. notes.xyz) while still blocking files
        #     whose browser-reported type is already specific and
        #     disallowed (e.g. application/x-msdownload for .exe).
        if content_type in _GENERIC_MIME_TYPES:
            logger.info(
                "Content type '%s' for '%s' not allowed; "
                "original browser type was generic. Falling back to text/plain.",
                resolved_type, original_filename,
            )
            resolved_type = "text/plain"

    if resolved_type not in allowed_types:
        raise ValidationError(
            f"File type '{resolved_type}' is not allowed. "
            f"Allowed types: {settings.UPLOAD_ALLOWED_TYPES}"
        )

    # 2. Validate size
    if len(file_bytes) > settings.UPLOAD_MAX_SIZE_BYTES:
        raise ValidationError(
            f"File size {len(file_bytes)} exceeds maximum "
            f"{settings.UPLOAD_MAX_SIZE_BYTES} bytes"
        )

    # 3. Determine if this is a temporary session
    is_temp = session_data.get("is_temporary", False)

    # 3a. Temporary sessions are rejected here.  The upload endpoint
    #     (upload_file in chat.py) promotes temp sessions to permanent
    #     before calling this function, so this branch should be
    #     unreachable under normal operation.
    if is_temp:
        raise ForbiddenError(
            "File uploads are not supported in temporary sessions. "
            "Please send a message first to create a permanent session."
        )

    # 4. Build storage path (resolve user/tenant from session_data for guests)
    file_id = str(uuid.uuid4())
    if current_user:
        uploader_id = current_user.id
        uploader_tenant_id = current_user.tenant_id
    else:
        # For guest/demo uploads, use any user from the tenant as owner
        from ..db.orm.users import User as UserORM
        result = await db.execute(
            select(UserORM).where(
                UserORM.tenant_id == session_data.get("tenant_id", "")
            ).limit(1)
        )
        first_user = result.scalar_one_or_none()
        if first_user:
            uploader_id = first_user.id
            uploader_tenant_id = first_user.tenant_id
        else:
            # Fallback: use any admin user across all tenants
            result = await db.execute(
                select(UserORM).where(UserORM.role == "admin").limit(1)
            )
            admin_user = result.scalar_one_or_none()
            if not admin_user:
                raise ValidationError("No user available for file upload")
            uploader_id = admin_user.id
            uploader_tenant_id = session_data.get("tenant_id", "unknown")
    bucket = f"{settings.MINIO_BUCKET_PREFIX}-{uploader_tenant_id}"
    safe_name = _sanitize_storage_filename(original_filename)
    key = (
        f"uploads/{uploader_id}/{session_data['id']}/"
        f"{file_id}-{safe_name}"
    )

    # 5. Upload to MinIO (use resolved_type for storage accuracy)
    await s3.ensure_bucket_exists(bucket)
    await s3.upload_object(bucket, key, file_bytes, resolved_type)

    # 5a. Extract text for document types via markitdown
    extracted_text: str | None = None
    if resolved_type in _EXTRACTABLE_MIME_TYPES:
        try:
            extracted_text = await _extract_text(
                file_bytes=file_bytes,
                filename=original_filename,
                content_type=resolved_type,
            )
        except Exception:
            # Best-effort: extraction failure should not block upload
            pass

    # 6. Persist DB row
    upload = FileUpload(
        id=file_id,
        tenant_id=uploader_tenant_id,
        user_id=uploader_id,
        session_id=None if is_temp else session_data["id"],
        original_filename=original_filename,
        content_type=resolved_type,
        size_bytes=len(file_bytes),
        storage_key=key,
        bucket=bucket,
        is_temporary=is_temp,
        extracted_text=extracted_text,
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)

    # 7. Track file ID in Redis for temp sessions (cleanup on delete / TTL expiry)
    if is_temp:
        from ..core.redis import get_temp_session, store_temp_session

        redis_data = await get_temp_session(session_data["id"])
        if redis_data is not None:
            uploaded_ids: list[str] = redis_data.get("uploaded_file_ids", [])
            uploaded_ids.append(file_id)
            redis_data["uploaded_file_ids"] = uploaded_ids
            await store_temp_session(session_data["id"], redis_data)

    # 8. Auto-index in RAG (background, best-effort)
    if extracted_text and not is_temp:
        try:
            from ..services.rag_service import index_document as _rag_index

            asyncio.ensure_future(_rag_index(
                db=await _fresh_db_session(),
                file_upload=upload,
            ))
            logger.info("Scheduled RAG indexing for file %s", file_id)
        except Exception:
            logger.warning("Failed to schedule RAG indexing for file %s", file_id, exc_info=True)

    return upload


async def list_uploads(
    db: AsyncSession,
    session_id: str,
    user_id: str,
    is_temporary: bool = False,
    file_ids: list[str] | None = None,
) -> list[FileUpload]:
    """List all file uploads for a session owned by a user.

    For temp sessions, *file_ids* should be the ``uploaded_file_ids``
    from the Redis session blob.  When omitted for temp sessions the
    result will be empty (prevents cross-session leakage).
    """
    if is_temporary and file_ids:
        result = await db.execute(
            select(FileUpload)
            .where(
                FileUpload.id.in_(file_ids),
                FileUpload.user_id == user_id,
            )
            .order_by(FileUpload.created_at.desc())
        )
    elif is_temporary:
        result = await db.execute(
            select(FileUpload).where(FileUpload.id.in_([]))
        )
    else:
        result = await db.execute(
            select(FileUpload)
            .where(
                FileUpload.session_id == session_id,
                FileUpload.user_id == user_id,
            )
            .order_by(FileUpload.created_at.desc())
        )
    return list(result.scalars().all())


async def get_upload_by_id(
    db: AsyncSession,
    file_id: str,
    user_id: str,
) -> FileUpload:
    """Get a single upload by ID.  Raises if not found or wrong owner."""
    result = await db.execute(
        select(FileUpload).where(FileUpload.id == file_id)
    )
    upload = result.scalar_one_or_none()

    if upload is None:
        raise NotFoundError("File upload not found")
    if upload.user_id != user_id:
        raise ForbiddenError("You do not own this file upload")
    return upload


async def generate_presigned_url(
    db: AsyncSession,
    file_id: str,
    user_id: str,
    expires_in: int = 900,
) -> str:
    """Generate a presigned download URL for an uploaded file."""
    upload = await get_upload_by_id(db, file_id, user_id)
    return await s3.generate_presigned_url(
        bucket=upload.bucket,
        key=upload.storage_key,
        expires_in=expires_in,
    )


async def delete_upload(
    db: AsyncSession,
    file_id: str,
    user_id: str,
) -> None:
    """Delete a file upload (MinIO object + DB row + RAG chunks)."""
    upload = await get_upload_by_id(db, file_id, user_id)

    # Clean up RAG document chunks first
    try:
        from ..services.rag_service import delete_document as _rag_delete
        await _rag_delete(db, file_id)
    except Exception:
        logger.warning("Failed to delete RAG chunks for file %s", file_id, exc_info=True)

    await s3.delete_object(bucket=upload.bucket, key=upload.storage_key)
    await db.execute(delete(FileUpload).where(FileUpload.id == file_id))
    await db.commit()


async def link_uploads_to_message(
    db: AsyncSession,
    file_ids: list[str],
    message_id: str,
    user_id: str,
) -> None:
    """Link pre-uploaded files to an assistant message.

    Only links rows owned by *user_id* and currently with
    ``message_id IS NULL``.
    """
    if not file_ids:
        return

    await db.execute(
        update(FileUpload)
        .where(
            FileUpload.id.in_(file_ids),
            FileUpload.user_id == user_id,
            FileUpload.message_id.is_(None),
        )
        .values(message_id=message_id)
    )
    await db.commit()


async def delete_uploads_for_session(
    db: AsyncSession,
    session_id: str,
) -> None:
    """Delete all file uploads (MinIO objects + DB rows) for a session.

    Used as cascade cleanup before deleting a session.
    Does NOT commit — the caller is responsible for committing.
    """
    result = await db.execute(
        select(FileUpload).where(FileUpload.session_id == session_id)
    )
    uploads = list(result.scalars().all())

    for upload in uploads:
        try:
            await s3.delete_object(bucket=upload.bucket, key=upload.storage_key)
        except Exception:
            pass  # Best-effort: MinIO object may already be gone

    if uploads:
        await db.execute(
            delete(FileUpload).where(FileUpload.session_id == session_id)
        )
        await db.flush()


async def delete_uploads_for_message(
    db: AsyncSession,
    message_id: str,
) -> None:
    """Delete all file uploads (MinIO objects + DB rows) linked to a message.

    Used as cascade cleanup before deleting a message.
    Does NOT commit — the caller is responsible for committing.
    """
    result = await db.execute(
        select(FileUpload).where(FileUpload.message_id == message_id)
    )
    uploads = list(result.scalars().all())

    for upload in uploads:
        try:
            await s3.delete_object(bucket=upload.bucket, key=upload.storage_key)
        except Exception:
            pass  # Best-effort

    if uploads:
        await db.execute(
            delete(FileUpload).where(FileUpload.message_id == message_id)
        )
        await db.flush()


async def list_uploads_for_message(
    db: AsyncSession,
    message_id: str,
) -> list[FileUpload]:
    """List all file uploads linked to a specific message."""
    result = await db.execute(
        select(FileUpload)
        .where(FileUpload.message_id == message_id)
        .order_by(FileUpload.created_at.asc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Pending upload re-linking (Issue #336)
# ---------------------------------------------------------------------------


async def link_pending_uploads_to_session(
    db: AsyncSession,
    session_id: str,
    user_id: str,
) -> int:
    """Re-associate temporary uploads with a now-permanent session.

    When a user uploads files to a pending (lazy) session before sending
    the first message, the uploads are stored with ``session_id = NULL``
    and ``is_temporary = True``.  This function links them to the permanent
    session created by ``_lazy_create_session`` so they remain accessible
    after promotion.

    Uses two strategies to find matching uploads:
    1. Primary: ``storage_key`` contains the *session_id* (reliable when
       the upload was created via ``upload_file`` which uses the URL session ID).
    2. Fallback: ``is_temporary = True`` + ``session_id IS NULL`` + recent
       (last 5 min).  Catches edge cases where the storage key pattern
       doesn't match (e.g. manual DB edits, key format changes).

    Returns the number of uploads re-linked.
    """
    from datetime import datetime, timezone, timedelta

    # Strategy 1: storage_key pattern match
    result = await db.execute(
        select(FileUpload).where(
            FileUpload.user_id == user_id,
            FileUpload.session_id.is_(None),
            FileUpload.is_temporary == True,  # noqa: E712
            FileUpload.storage_key.like(f"%/{session_id}/%"),
        )
    )
    uploads = list(result.scalars().all())
    linked_ids = {u.id for u in uploads}

    # Strategy 2: fallback by recency (uploads created in the last 5 min)
    recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    fallback_result = await db.execute(
        select(FileUpload).where(
            FileUpload.user_id == user_id,
            FileUpload.session_id.is_(None),
            FileUpload.is_temporary == True,  # noqa: E712
            FileUpload.created_at >= recent_cutoff,
            FileUpload.id.notin_(linked_ids) if linked_ids else True,
        )
    )
    fallback_uploads = [
        u for u in fallback_result.scalars().all()
        if u.id not in linked_ids
    ]
    if fallback_uploads:
        logger.info(
            "link_pending_uploads: fallback matched %d additional upload(s) for session %s",
            len(fallback_uploads), session_id,
        )
        uploads.extend(fallback_uploads)

    for upload in uploads:
        upload.session_id = session_id
        upload.is_temporary = False

    if uploads:
        await db.flush()
        logger.info(
            "Re-linked %d pending upload(s) to session %s",
            len(uploads), session_id,
        )

    return len(uploads)


# ---------------------------------------------------------------------------
# Temporary session cleanup
# ---------------------------------------------------------------------------


async def delete_orphaned_temp_uploads(db: AsyncSession) -> None:
    """Delete file uploads belonging to temp sessions whose Redis TTL has expired.

    Queries ``FileUpload`` rows where ``is_temporary = True`` and
    ``created_at`` is older than ``TEMPORARY_SESSION_TTL_SECONDS`` plus a
    1-hour grace period, then removes the MinIO object and DB row.
    Commits at the end.
    """
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.TEMPORARY_SESSION_TTL_SECONDS + 3600  # +1h grace
    )

    result = await db.execute(
        select(FileUpload).where(
            FileUpload.is_temporary == True,  # noqa: E712
            FileUpload.created_at < cutoff,
        )
    )
    uploads = list(result.scalars().all())

    for upload in uploads:
        try:
            await s3.delete_object(bucket=upload.bucket, key=upload.storage_key)
        except Exception:
            pass  # Best-effort: MinIO object may already be gone

    if uploads:
        ids = [u.id for u in uploads]
        await db.execute(delete(FileUpload).where(FileUpload.id.in_(ids)))
        await db.commit()


async def _delete_temp_upload_by_id(db: AsyncSession, file_id: str) -> None:
    """Delete a single temp upload by file_id.  Not exposed as an endpoint."""
    result = await db.execute(
        select(FileUpload).where(FileUpload.id == file_id)
    )
    upload = result.scalar_one_or_none()
    if upload is None:
        return
    try:
        await s3.delete_object(bucket=upload.bucket, key=upload.storage_key)
    except Exception:
        pass
    await db.execute(delete(FileUpload).where(FileUpload.id == file_id))
    await db.flush()


async def delete_demo_temp_uploads(db: AsyncSession) -> int:
    """Delete all temporary file uploads belonging to the demo tenant.

    Returns the number of uploads deleted.  Runs more frequently than the
    general orphan cleanup (every 6 hours vs every 24 hours) so demo data
    doesn't linger in the DB and MinIO.
    """
    from ..services.tenant_service import get_demo_tenant

    tenant = await get_demo_tenant(db)
    if tenant is None:
        return 0

    result = await db.execute(
        select(FileUpload).where(
            FileUpload.tenant_id == tenant.id,
            FileUpload.is_temporary == True,  # noqa: E712
        )
    )
    uploads = list(result.scalars().all())

    for upload in uploads:
        try:
            await s3.delete_object(bucket=upload.bucket, key=upload.storage_key)
        except Exception:
            pass  # Best-effort: MinIO object may already be gone

    if uploads:
        ids = [u.id for u in uploads]
        await db.execute(delete(FileUpload).where(FileUpload.id.in_(ids)))
        await db.commit()

    logger.info("Deleted %d temporary file uploads for demo tenant %s", len(uploads), tenant.id)
    return len(uploads)


# ---------------------------------------------------------------------------
# Fresh DB session for background tasks
# ---------------------------------------------------------------------------


async def _fresh_db_session() -> AsyncSession:
    """Create a new async DB session independent of the current request.

    Used by ``asyncio.ensure_future`` background tasks (e.g. RAG indexing)
    that outlive the request-response cycle and therefore can't reuse
    the request-scoped ``db`` session.
    """
    from ..db.base import AsyncSessionLocal
    return AsyncSessionLocal()


# ---------------------------------------------------------------------------
# Text extraction (markitdown)
# ---------------------------------------------------------------------------


async def _extract_text(
    file_bytes: bytes,
    filename: str,
    content_type: str,
) -> str:
    """Extract text from a document using markitdown.

    Writes bytes to a temp file so markitdown can use the filename
    extension to pick the correct converter.
    """
    import asyncio

    suffix = _get_suffix(filename)

    def _sync_extract() -> str:
        from markitdown import MarkItDown

        md = MarkItDown()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            result = md.convert(tmp_path)
            return result.text_content
        finally:
            os.unlink(tmp_path)

    return await asyncio.to_thread(_sync_extract)


def _get_suffix(filename: str) -> str:
    """Return a safe file suffix for temp file creation."""
    _, ext = os.path.splitext(filename)
    if ext and len(ext) <= 10:
        return ext
    return ""
