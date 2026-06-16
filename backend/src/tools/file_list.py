# =============================================================================
# PH Agent Hub — File List Tool (Built-in)
# =============================================================================
# Always-available discovery tool that lets the agent inspect uploaded files.
# Not stored in the DB — unconditionally appended in _resolve_tool_callables().
# =============================================================================

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from agent_framework import tool

from ..storage.s3 import download_object

logger = logging.getLogger(__name__)

MAX_FILE_CONTENT_CHARS = 50_000
MAX_BASE64_BYTES = 20 * 1024 * 1024  # 20 MB — cap raw bytes for base64 encoding


def build_file_list_tool(
    db: AsyncSession,
    session_id: str,
    tenant_id: str,
    is_temporary: bool = False,
    uploaded_file_ids: list[str] | None = None,
) -> list:
    """Return built-in MAF @tools for file discovery and content reading.

    Args:
        db: Active async DB session.
        session_id: Current session ID.
        tenant_id: Current tenant ID.
        is_temporary: Whether the session is temporary (changes DB query).
        uploaded_file_ids: Specific file IDs for temp sessions (from Redis).
    """
    from ..db.orm.file_uploads import FileUpload

    @tool
    async def list_uploaded_files() -> list[dict[str, Any]]:
        """List all files uploaded in this conversation session.

        Returns ``file_id``, ``filename``, ``content_type``, ``size_kb``,
        and a short ``preview`` (first 200 characters) for each file.

        If the user asks you to attach a file to an email, use the
        ``file_id`` directly in ``send_email`` / ``reply_email`` /
        ``save_draft`` — you do NOT need to call ``read_file_content``
        first.  The ``preview`` and ``filename`` already tell you what
        the file contains.

        Call ``read_file_content(file_id)`` only when you need to
        ANALYZE or SUMMARIZE the file's text content.  For attachments,
        just pass ``file_ids`` to the email tool.
        """
        if is_temporary and uploaded_file_ids:
            result = await db.execute(
                select(FileUpload).where(
                    FileUpload.id.in_(uploaded_file_ids),
                    FileUpload.tenant_id == tenant_id,
                )
            )
        elif is_temporary:
            # No file IDs tracked — return empty
            result = await db.execute(
                select(FileUpload).where(
                    FileUpload.id.in_([]),
                )
            )
        else:
            result = await db.execute(
                select(FileUpload).where(
                    FileUpload.session_id == session_id,
                    FileUpload.tenant_id == tenant_id,
                )
            )
        uploads = result.scalars().all()

        files: list[dict[str, Any]] = []
        for u in uploads:
            kb_size = u.size_bytes / 1024
            preview = ""
            if u.extracted_text:
                preview = u.extracted_text[:200]
                if len(u.extracted_text) > 200:
                    preview += "..."
            files.append({
                "file_id": u.id,
                "filename": u.original_filename,
                "content_type": u.content_type,
                "size_kb": round(kb_size, 1),
                "preview": preview,
            })

        logger.debug(
            "list_uploaded_files session=%s → %d files", session_id, len(files)
        )
        return files

    @tool
    async def read_file_content(file_id: str) -> dict[str, Any]:
        """Read and analyze the full text content of an uploaded file.

        Use this ONLY when you need to SUMMARIZE, ANALYZE, or answer
        questions about a file's content.  This returns the extracted
        text — it is NOT suitable for email attachments.

        For EMAIL ATTACHMENTS: do NOT call this tool.  Instead, use
        the ``file_ids`` parameter of ``send_email`` / ``reply_email`` /
        ``save_draft`` directly.  The ``list_uploaded_files`` preview
        already tells you enough about the file.

        Args:
            file_id: The file ID returned by ``list_uploaded_files()``.

        Returns a dict with ``filename``, ``content_type``, ``text`` (the full
        extracted text), ``truncated`` (bool), and optionally ``error``.
        """.format(max_chars=MAX_FILE_CONTENT_CHARS)

        try:
            UUID(file_id)  # validate format only
        except ValueError:
            return {"error": f"Invalid file_id: {file_id!r}"}

        result = await db.execute(
            select(FileUpload).where(
                FileUpload.id == file_id,
                FileUpload.tenant_id == tenant_id,
            )
        )
        upload = result.scalar_one_or_none()

        if upload is None:
            return {"error": f"File not found: {file_id}"}

        has_text = bool(upload.extracted_text)
        if not has_text:
            return {
                "file_id": upload.id,
                "filename": upload.original_filename,
                "content_type": upload.content_type,
                "text": "",
                "truncated": False,
                "note": "No text could be extracted from this file.",
            }

        text = upload.extracted_text
        truncated = len(text) > MAX_FILE_CONTENT_CHARS
        if truncated:
            text = text[:MAX_FILE_CONTENT_CHARS]

        logger.debug(
            "read_file_content file_id=%s → %d chars (truncated=%s)",
            file_id, len(text), truncated,
        )
        return {
            "file_id": upload.id,
            "filename": upload.original_filename,
            "content_type": upload.content_type,
            "text": text,
            "truncated": truncated,
        }

    @tool
    async def download_file_for_attachment(file_id: str) -> dict[str, Any]:
        """Download the raw bytes of a previously uploaded file and return
        them as base64 for use as an email attachment.

        Use this ONLY when you need to attach a file to an email via
        ``send_email`` / ``reply_email`` / ``save_draft`` and cannot use
        the ``file_ids`` parameter of those tools directly.  The returned
        ``content_base64`` is the ``content`` field for the attachment dict.

        PREFERRED ALTERNATIVE: Pass ``file_ids`` directly to the email
        tool (``send_email`` / ``reply_email`` / ``save_draft``) and let
        it fetch the file bytes internally — no base64 needed in your
        response.  Use ``download_file_for_attachment`` only when you need
        to inspect or modify the file content before attaching.

        For reading the text contents of a file, use ``read_file_content``
        instead — it's faster and doesn't include the raw binary data.

        Args:
            file_id: The file ID returned by ``list_uploaded_files()``.

        Returns a dict with ``filename``, ``content_type``, ``content_base64``
        (the raw file bytes, base64-encoded), ``size_bytes``, and optionally
        ``error`` or ``base64_truncated``.
        """.format(max_chars=MAX_FILE_CONTENT_CHARS)
        import base64

        try:
            UUID(file_id)
        except ValueError:
            return {"error": f"Invalid file_id: {file_id!r}"}

        fu_result = await db.execute(
            select(FileUpload).where(
                FileUpload.id == file_id,
                FileUpload.tenant_id == tenant_id,
            )
        )
        upload = fu_result.scalar_one_or_none()

        if upload is None:
            return {"error": f"File not found: {file_id}"}

        content_base64 = ""
        download_error: str | None = None
        base64_truncated = False
        try:
            raw_bytes = await download_object(upload.bucket, upload.storage_key)
            if len(raw_bytes) > MAX_BASE64_BYTES:
                base64_truncated = True
                logger.warning(
                    "File %s (%d bytes) exceeds base64 cap — truncating",
                    file_id, len(raw_bytes),
                )
                raw_bytes = raw_bytes[:MAX_BASE64_BYTES]
            content_base64 = base64.b64encode(raw_bytes).decode("ascii")
        except Exception as exc:
            download_error = f"Could not download file from storage: {exc}"
            logger.warning(
                "Could not download file %s from storage (bucket=%s, key=%s): %s",
                file_id, upload.bucket, upload.storage_key, exc,
            )

        result: dict = {
            "file_id": upload.id,
            "filename": upload.original_filename,
            "content_type": upload.content_type,
            "size_bytes": upload.size_bytes or len(raw_bytes) if not download_error else 0,
            "content_base64": content_base64,
        }

        if base64_truncated:
            result["base64_truncated"] = True
            result["base64_note"] = (
                f"File exceeds {MAX_BASE64_BYTES // (1024*1024)} MB — "
                "content_base64 contains only the first 20 MB."
            )

        if download_error:
            result["download_error"] = download_error
            result["content_base64"] = ""

        return result

    return [list_uploaded_files, read_file_content, download_file_for_attachment]
