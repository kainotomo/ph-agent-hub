# =============================================================================
# PH Agent Hub — Shared "save instead of return" helper (Issue #626)
# =============================================================================
# ``extract_pdf`` and ``fetch_url`` are the two tools that pull outside text
# into a session, and MAF replays every tool result into the next step's
# prompt — so a document returned inline is re-billed on every later call of
# the turn.  With ``save_to`` the text is stored once and the tool result
# carries a small manifest instead.
#
# The store is the existing tenant-scoped one (``file_uploads`` + MinIO), so
# the identifier handed back is a ``file_id``, not a path: there is no
# caller-supplied path, hence no traversal surface, and the store is shared
# across replicas.
#
# Reading back is the already-built ``read_file_content(file_id, pattern=...)``
# tool from ``file_list.py`` — no new tool definition is added.
# =============================================================================

import asyncio
import hashlib
import logging
import re
from typing import Any
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

# Saving touches the runner's single ``AsyncSession``.  MAF runs a parallel
# tool batch concurrently (a prompt may ask for three pages at once), so two
# ``save_to`` calls in one batch would collide with "Session is already
# flushing".  Same guard as ``tools/memory.py`` (Issue #583), but shared by
# both tools so a mixed batch is covered too.  An ``asyncio.Lock`` must not be
# shared across loops, hence one lock per running loop.
_save_locks: dict[int, asyncio.Lock] = {}


def _save_lock() -> asyncio.Lock:
    """Return the save lock belonging to the current event loop."""
    loop = asyncio.get_running_loop()
    lock = _save_locks.get(id(loop))
    if lock is None:
        lock = asyncio.Lock()
        _save_locks[id(loop)] = lock
    return lock

# Acceptance (#626): the preview must be at most 500 characters.
PREVIEW_CHARS: int = 500
MAX_FILENAME_CHARS: int = 80

# Stable, machine-readable reasons.  The tool layer returns them verbatim so
# the model can tell a quota problem from a storage failure without parsing
# prose.
CODE_UNAVAILABLE: str = "save_to_unavailable"
CODE_EMPTY_TEXT: str = "save_to_empty_text"
CODE_TOO_LARGE: str = "save_to_too_large"
CODE_QUOTA: str = "save_to_quota_exceeded"
CODE_FAILED: str = "save_to_failed"


def text_digest(text: str) -> str:
    """Return the SHA-256 hex digest of *text* as it is stored (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    """Reduce *value* to a safe, short filename fragment."""
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    value = value.strip("-._")[:MAX_FILENAME_CHARS]
    return value.strip("-._")


def filename_for_url(url: str, *, fallback: str = "document.txt") -> str:
    """Derive a readable ``.txt`` filename from *url*.

    ``https://www.sec.gov/Archives/edgar/data/123/report.pdf`` becomes
    ``sec.gov-report.txt``.  Deterministic for the same input.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return fallback
    if host.startswith("www."):
        host = host[4:]

    stem = ""
    path = unquote(parsed.path or "").rstrip("/")
    if path:
        segment = path.rsplit("/", 1)[-1]
        base, dot, ext = segment.rpartition(".")
        if dot and 1 <= len(ext) <= 5:
            segment = base
        stem = segment

    slug = _slug("-".join(part for part in (host, stem) if part))
    return f"{slug}.txt" if slug else fallback


def _code_for(message: str) -> str:
    """Map a ``ValidationError`` message from the service to a stable code."""
    lowered = message.lower()
    if "empty document" in lowered:
        return CODE_EMPTY_TEXT
    if "save limit reached" in lowered:
        return CODE_QUOTA
    if "save limit" in lowered:
        return CODE_TOO_LARGE
    return CODE_FAILED


async def save_extracted_text(
    db: Any,
    *,
    text: str,
    url: str,
    filename: str,
    session_id: str,
    tenant_id: str,
    user_id: str | None,
    is_temporary: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist *text* and return a manifest — never the text itself.

    Args:
        db: The runner's ``AsyncSession``.  ``None`` disables saving.
        text: The extracted document text to store.
        url: The source URL, echoed into the manifest.
        filename: Display name for the stored document.
        session_id: Owning session (used for the storage key and the quota).
        tenant_id: Tenant that owns the row.
        user_id: Uploader; the row requires one.
        is_temporary: Temporary sessions store ``session_id=None`` and track
            the id in Redis instead.
        extra: Tool-specific metadata (``status_code``, ``pages``, ``title``)
            merged into the manifest.

    Returns:
        On success, a manifest with ``file_id``, ``filename``, ``chars``,
        ``sha256``, ``preview`` (≤ 500 chars) and ``saved=True``.
        On failure, ``{url, error, error_code}``.
    """
    if db is None or not session_id or not tenant_id or not user_id:
        return {
            "url": url,
            "error": (
                "Saving is not available in this context. Call the tool "
                "again without save_to to get the text inline."
            ),
            "error_code": CODE_UNAVAILABLE,
        }

    if not text or not text.strip():
        return {
            "url": url,
            "error": "The document produced no text, so there is nothing to save.",
            "error_code": CODE_EMPTY_TEXT,
        }

    # Imported lazily: the service pulls in storage/config, which the tool
    # modules should not require at import time.
    from ..core.exceptions import ValidationError
    from ..services.upload_service import create_text_upload

    try:
        # Serialise the write: a parallel tool batch shares one AsyncSession.
        async with _save_lock():
            upload = await create_text_upload(
                db,
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                filename=filename,
                text=text,
                is_temporary=is_temporary,
            )
    except ValidationError as exc:
        logger.info("save_to rejected for %s: %s", url, exc.message)
        return {
            "url": url,
            "error": exc.message,
            "error_code": _code_for(exc.message),
        }
    except Exception as exc:  # noqa: BLE001 - surfaced to the model verbatim
        logger.exception("save_to failed for %s", url)
        return {
            "url": url,
            "error": f"Could not save the document: {exc}",
            "error_code": CODE_FAILED,
        }

    manifest: dict[str, Any] = {"url": url}
    if extra:
        manifest.update(extra)
    manifest.update(
        {
            "file_id": upload.id,
            "filename": upload.original_filename,
            "chars": len(text),
            "sha256": text_digest(text),
            "preview": text[:PREVIEW_CHARS],
            "saved": True,
            "hint": (
                "The text was saved, not returned. Read only the lines you "
                "need: read_file_content(file_id, pattern='...')."
            ),
        }
    )
    logger.info(
        "save_to stored %d chars as file %s (session=%s)",
        len(text), upload.id, session_id,
    )
    return manifest
