# =============================================================================
# PH Agent Hub — File List Tool (Built-in)
# =============================================================================
# Always-available discovery tool that lets the agent inspect uploaded files.
# Not stored in the DB — unconditionally appended in _resolve_tool_callables().
# =============================================================================

import logging
import re
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from agent_framework import tool

from ..storage.s3 import download_object

logger = logging.getLogger(__name__)

MAX_FILE_CONTENT_CHARS = 50_000
MAX_BASE64_BYTES = 20 * 1024 * 1024  # 20 MB — cap raw bytes for base64 encoding


async def _new_read_session() -> AsyncSession:
    """Open a short-lived session for reading committed file rows (Issue #635).

    The agent runner's session holds a REPEATABLE READ snapshot taken before
    ``save_to`` committed its row on another session; a read on that session
    would not see the new file.  Reads are independent of the writer, so the
    runner can ask for a fresh session instead.
    """
    from ..db.base import AsyncSessionLocal

    return AsyncSessionLocal()


def build_file_list_tool(
    db: AsyncSession,
    session_id: str,
    tenant_id: str,
    is_temporary: bool = False,
    uploaded_file_ids: list[str] | None = None,
    *,
    fresh_session_reads: bool = False,
) -> list:
    """Return built-in MAF @tools for file discovery and content reading.

    Args:
        db: Active async DB session.
        session_id: Current session ID.
        tenant_id: Current tenant ID.
        is_temporary: Whether the session is temporary (changes DB query).
        uploaded_file_ids: Specific file IDs for temp sessions (from Redis).
        fresh_session_reads: When True, read rows through a short-lived
            session of our own instead of the caller's session.  The runner
            sets this because its session may predate a ``save_to`` commit
            (Issue #635); direct callers/tests keep the default.
    """
    from ..db.orm.file_uploads import FileUpload

    @asynccontextmanager
    async def _read_db():
        """Yield the session a file read should use."""
        if not fresh_session_reads:
            yield db
            return
        session = await _new_read_session()
        try:
            yield session
        finally:
            try:
                await session.close()
            except Exception:
                logger.debug(
                    "file_list: failed to close fresh read session",
                    exc_info=True,
                )

    def _snapshot(upload: Any) -> dict[str, Any]:
        """Copy the columns the tools need before the session closes."""
        return {
            "id": upload.id,
            "filename": upload.original_filename,
            "content_type": upload.content_type,
            "size_bytes": upload.size_bytes,
            "extracted_text": upload.extracted_text,
            "bucket": upload.bucket,
            "storage_key": upload.storage_key,
        }

    async def _load_upload(file_id: str) -> dict[str, Any] | None:
        async with _read_db() as read_db:
            result = await read_db.execute(
                select(FileUpload).where(
                    FileUpload.id == file_id,
                    FileUpload.tenant_id == tenant_id,
                )
            )
            upload = result.scalar_one_or_none()
            return _snapshot(upload) if upload is not None else None

    async def _load_session_uploads() -> list[dict[str, Any]]:
        async with _read_db() as read_db:
            if is_temporary and uploaded_file_ids:
                result = await read_db.execute(
                    select(FileUpload).where(
                        FileUpload.id.in_(uploaded_file_ids),
                        FileUpload.tenant_id == tenant_id,
                    )
                )
            elif is_temporary:
                # No file IDs tracked — return empty
                result = await read_db.execute(
                    select(FileUpload).where(
                        FileUpload.id.in_([]),
                    )
                )
            else:
                result = await read_db.execute(
                    select(FileUpload).where(
                        FileUpload.session_id == session_id,
                        FileUpload.tenant_id == tenant_id,
                    )
                )
            return [_snapshot(u) for u in result.scalars().all()]

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
        uploads = await _load_session_uploads()

        files: list[dict[str, Any]] = []
        for u in uploads:
            kb_size = (u["size_bytes"] or 0) / 1024
            preview = ""
            if u["extracted_text"]:
                preview = u["extracted_text"][:200]
                if len(u["extracted_text"]) > 200:
                    preview += "..."
            files.append({
                "file_id": u["id"],
                "filename": u["filename"],
                "content_type": u["content_type"],
                "size_kb": round(kb_size, 1),
                "preview": preview,
            })

        logger.debug(
            "list_uploaded_files session=%s → %d files", session_id, len(files)
        )
        return files

    @tool
    async def read_file_content(
        file_id: str,
        pattern: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Read an uploaded file, optionally only the lines that matter.

        ALWAYS pass ``pattern`` when you are looking for something specific
        in a long document.  A saved PDF or web page can be tens of thousands
        of characters; asking for the whole thing puts all of it into the
        conversation, where it is re-sent on every later model call of the
        turn.  ``pattern="Total revenue"`` answers the same question with a
        handful of lines.

        Use this ONLY when you need to SUMMARIZE, ANALYZE, or answer
        questions about a file's content.  This returns text — it is NOT
        suitable for email attachments.

        For EMAIL ATTACHMENTS: do NOT call this tool.  Instead, use
        the ``file_ids`` parameter of ``send_email`` / ``reply_email`` /
        ``save_draft`` directly.  The ``list_uploaded_files`` preview
        already tells you enough about the file.

        Args:
            file_id: The file ID returned by ``list_uploaded_files()``, or
                the ``file_id`` from a tool result that saved a document.
            pattern: Optional case-insensitive regular expression.  When
                given, only matching lines are returned.
            offset: Number of selected lines to skip (0-based) — use it to
                page through a long result.
            limit: Maximum number of lines to return.  Omit it (with no
                ``pattern``) to get the legacy full-text behaviour.

        Returns a dict with ``filename``, ``content_type``, ``text`` (the
        selected lines, numbered when a selection was requested),
        ``truncated`` (bool), ``total_lines``, ``returned_lines``,
        ``first_line``/``last_line`` (1-based), ``matched_lines`` when a
        ``pattern`` was supplied, and ``error``/``error_code`` on failure.
        """
        try:
            UUID(file_id)  # validate format only
        except ValueError:
            return {
                "error": f"Invalid file_id: {file_id!r}",
                "error_code": "invalid_file_id",
            }

        record = await _load_upload(file_id)

        # A file owned by another tenant never reaches this point: the query
        # is already scoped by ``tenant_id``, so both cases look identical.
        if record is None:
            return {
                "error": f"File not found: {file_id}",
                "error_code": "file_not_found",
            }

        has_text = bool(record["extracted_text"])
        if not has_text:
            return {
                "file_id": record["id"],
                "filename": record["filename"],
                "content_type": record["content_type"],
                "text": "",
                "truncated": False,
                "note": "No text could be extracted from this file.",
            }

        full_text: str = record["extracted_text"]
        selecting = pattern is not None or offset > 0 or limit is not None

        # ---- Legacy path: nothing selected, return today's exact shape ----
        if not selecting:
            text = full_text
            truncated = len(text) > MAX_FILE_CONTENT_CHARS
            if truncated:
                text = text[:MAX_FILE_CONTENT_CHARS]

            logger.debug(
                "read_file_content file_id=%s → %d chars (truncated=%s)",
                file_id, len(text), truncated,
            )
            return {
                "file_id": record["id"],
                "filename": record["filename"],
                "content_type": record["content_type"],
                "text": text,
                "truncated": truncated,
            }

        # ---- Selection path: pattern and/or a line window ----
        lines = full_text.splitlines()
        total_lines = len(lines)

        if pattern:
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                return {
                    "error": f"Invalid regular expression: {exc}",
                    "error_code": "invalid_pattern",
                }
            matched: list[tuple[int, str]] = [
                (number, line)
                for number, line in enumerate(lines, start=1)
                if regex.search(line)
            ]
        else:
            matched = list(enumerate(lines, start=1))
        matched_lines = len(matched)

        start = max(0, offset)
        end = None if limit is None else start + max(0, limit)
        window = matched[start:end]

        rendered = "\n".join(f"{number}: {line}" for number, line in window)
        truncated = len(rendered) > MAX_FILE_CONTENT_CHARS
        if truncated:
            rendered = rendered[:MAX_FILE_CONTENT_CHARS]

        logger.debug(
            "read_file_content file_id=%s pattern=%r → %d/%d lines",
            file_id, pattern, len(window), matched_lines,
        )
        payload: dict[str, Any] = {
            "file_id": record["id"],
            "filename": record["filename"],
            "content_type": record["content_type"],
            "text": rendered,
            "truncated": truncated,
            "offset": start,
            "limit": limit,
            "total_lines": total_lines,
            "returned_lines": len(window),
            "first_line": window[0][0] if window else None,
            "last_line": window[-1][0] if window else None,
        }
        if pattern:
            payload["pattern"] = pattern
            payload["matched_lines"] = matched_lines
        return payload

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

        record = await _load_upload(file_id)

        if record is None:
            return {"error": f"File not found: {file_id}"}

        content_base64 = ""
        download_error: str | None = None
        base64_truncated = False
        try:
            raw_bytes = await download_object(
                record["bucket"], record["storage_key"],
            )
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
                file_id, record["bucket"], record["storage_key"], exc,
            )

        result: dict = {
            "file_id": record["id"],
            "filename": record["filename"],
            "content_type": record["content_type"],
            "size_bytes": (
                (record["size_bytes"] or len(raw_bytes))
                if not download_error
                else 0
            ),
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
