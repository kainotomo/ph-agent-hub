# =============================================================================
# PH Agent Hub — Host File Reader (Built-in, Issue #658)
# =============================================================================
# Reads text files that live on the hub host — a repository prompt, a CSV
# under ``data/``, a generated report — one line page at a time.
#
# Before this tool the only reader for host files was a shell/MCP call, which
# is capped at TOOL_OUTPUT_CAP_MAX_CHARS, decodes with whatever code page the
# shell happens to use (PowerShell 5.1 mojibake for UTF-8) and offers no
# offset/limit, so the model paged a 176 KB file by array-index guesswork.
#
# Design:
#   * the tool is registered only when WORKSPACE_ROOTS names at least one
#     existing directory (empty default = no host-file access);
#   * every path is resolved and checked against that allowlist — ``..``
#     traversal, symlinks and non-regular files are refused with a named
#     error instead of an empty result;
#   * the file is opened read-only and decoded explicitly (UTF-8 default);
#   * one call stays under READ_FILE_MAX_CHARS, safely below the 40,000-char
#     live tool-output cap, so a page never enters the middle-dropping
#     truncation path;
#   * the response carries ``continue_at`` (next 1-based line), so a large
#     file is paged with arithmetic rather than a search.
# =============================================================================

import codecs
import logging
import os
import stat
from pathlib import Path
from typing import Any

from agent_framework import tool

from ..core.config import settings

logger = logging.getLogger(__name__)

# Per-call budget.  600 lines and 30,000 characters are both well below the
# 40,000-character TOOL_OUTPUT_CAP_MAX_CHARS ceiling (and below the per-model
# proportional cap), so a normal page is never truncated.
READ_FILE_MAX_LINES = 600
READ_FILE_DEFAULT_LIMIT = 600
READ_FILE_MAX_CHARS = 30_000


def _parse_roots(roots: list[str] | str | None) -> list[Path]:
    """Normalise the workspace-root allowlist into resolved directories.

    ``roots`` may be a list (tests, callers) or a comma-separated string
    (the ``WORKSPACE_ROOTS`` setting).  ``None`` reads the setting.  Entries
    that are not existing directories are ignored with a warning, so a typo
    narrows the allowlist rather than widening it.
    """
    if roots is None:
        chunks: list[str] = [settings.WORKSPACE_ROOTS]
    elif isinstance(roots, str):
        chunks = [roots]
    else:
        chunks = list(roots)

    parsed: list[Path] = []
    for chunk in chunks:
        for part in str(chunk or "").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                real = Path(os.path.realpath(os.path.expanduser(part)))
            except (OSError, RuntimeError, ValueError):
                logger.warning("read_file: ignoring invalid workspace root %r", part)
                continue
            if not real.is_dir():
                logger.warning(
                    "read_file: workspace root is not a directory: %s", real,
                )
                continue
            if real not in parsed:
                parsed.append(real)
    return parsed


def _fail(error_code: str, message: str) -> dict[str, Any]:
    """Return a named tool error (never an empty success payload)."""
    return {"error": message, "error_code": error_code}


def _is_within(path: Path, root: Path) -> bool:
    """True when *path* is inside *root* or is the root itself.

    The root itself is allowed here so a request for a directory reaches the
    stat check and is refused as ``not_a_regular_file`` instead of as a
    confusing allowlist violation.
    """
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_host_path(
    path: str,
    roots: list[Path],
) -> tuple[Path, os.stat_result] | dict[str, Any]:
    """Resolve *path* inside the allowlist or return a named error.

    Relative paths are tried against every configured root (first match
    wins) and then resolved lexically.  Absolute paths are used as-is.  In
    both cases the resolved path must stay inside one of the roots.
    """
    if not roots:
        return _fail(
            "workspace_not_configured",
            "Host file access is disabled: no workspace roots are configured "
            "(set WORKSPACE_ROOTS).",
        )
    if not isinstance(path, str) or not path.strip():
        return _fail("invalid_path", "path must be a non-empty string")
    if "\x00" in path:
        return _fail("invalid_path", "path contains a NUL byte")

    requested = Path(path).expanduser()
    if ".." in requested.parts:
        return _fail(
            "path_traversal",
            f"Path traversal ('..') is not allowed: {path!r}",
        )

    if requested.is_absolute():
        candidate = requested
    else:
        existing = [
            root / requested
            for root in roots
            if os.path.lexists(root / requested)
        ]
        if len(existing) > 1:
            logger.info(
                "read_file: %r exists under %d workspace roots; using %s",
                path, len(existing), existing[0],
            )
        candidate = existing[0] if existing else roots[0] / requested

    if os.path.islink(candidate):
        return _fail(
            "symlink_not_allowed",
            f"Symbolic links are not allowed: {path!r}",
        )

    try:
        resolved = Path(os.path.realpath(candidate))
    except (OSError, RuntimeError) as exc:  # pragma: no cover - defensive
        return _fail("invalid_path", f"Could not resolve {path!r}: {exc}")

    if not any(_is_within(resolved, root) for root in roots):
        return _fail(
            "path_not_allowed",
            f"Path is outside the configured workspace roots: {path!r}",
        )

    try:
        st = resolved.stat()
    except FileNotFoundError:
        return _fail("file_not_found", f"File not found: {path!r}")
    except OSError as exc:
        return _fail("file_read_error", f"Could not read {path!r}: {exc}")

    if not stat.S_ISREG(st.st_mode):
        return _fail("not_a_regular_file", f"Not a regular file: {path!r}")
    return resolved, st


def _read_file_page(
    path: str,
    *,
    offset: int,
    limit: int | None,
    encoding: str,
    roots: list[Path],
) -> dict[str, Any]:
    """Read one line page of *path* (see ``build_read_file_tool``)."""
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 1:
        return _fail(
            "invalid_offset",
            "offset must be an integer >= 1 (1-based line number).",
        )
    if limit is None:
        requested_limit = READ_FILE_DEFAULT_LIMIT
        effective_limit = READ_FILE_DEFAULT_LIMIT
    else:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return _fail("invalid_limit", "limit must be a positive integer.")
        requested_limit = limit
        effective_limit = min(limit, READ_FILE_MAX_LINES)

    try:
        codec = codecs.lookup(encoding)
    except (LookupError, TypeError):
        return _fail("invalid_encoding", f"Unknown encoding: {encoding!r}")
    codec_name = codec.name

    resolved = _resolve_host_path(path, roots)
    if isinstance(resolved, dict):
        return resolved
    resolved_path, st = resolved

    try:
        raw = resolved_path.read_bytes()
    except OSError as exc:
        return _fail("file_read_error", f"Could not read {path!r}: {exc}")

    try:
        text = raw.decode(codec_name)
    except UnicodeDecodeError as exc:
        return _fail(
            "decode_error",
            f"{path!r} is not valid {codec_name} (byte {exc.start}: "
            f"{exc.reason}). Pass encoding explicitly when the file uses a "
            "different code page (e.g. encoding='cp1252').",
        )

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # a trailing newline terminates the last line, not a new one
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]
    total_lines = len(lines)

    if offset > total_lines:
        return {
            "path": str(resolved_path),
            "encoding": codec_name,
            "text": "",
            "offset": offset,
            "limit": requested_limit,
            "first_line": None,
            "last_line": None,
            "total_lines": total_lines,
            "total_bytes": st.st_size,
            "truncated": False,
            "continue_at": None,
            "note": (
                f"offset {offset} is past the end of the file "
                f"({total_lines} lines)."
            ),
        }

    start = offset - 1
    end = min(total_lines, start + effective_limit)
    page: list[str] = []
    used = 0
    cut_line = False
    index = start
    while index < end:
        rendered = f"{index + 1}: {lines[index]}"
        cost = len(rendered) + (1 if page else 0)  # "\n" between lines
        if used + cost > READ_FILE_MAX_CHARS:
            if not page:
                # A single line longer than a whole page: keep a marked prefix
                # of that line rather than dropping it or blowing the cap.
                page.append(rendered[:READ_FILE_MAX_CHARS])
                cut_line = True
                index += 1
            break
        page.append(rendered)
        used += cost
        index += 1

    returned = len(page)
    last_line = start + returned if returned else None
    requested_last = min(total_lines, start + effective_limit)
    truncated = last_line is not None and last_line < requested_last
    continue_at = (
        last_line + 1
        if last_line is not None and last_line < total_lines
        else None
    )

    logger.debug(
        "read_file path=%s offset=%d limit=%d → lines %s-%s of %d",
        resolved_path, offset, requested_limit, offset, last_line, total_lines,
    )

    result: dict[str, Any] = {
        "path": str(resolved_path),
        "encoding": codec_name,
        "text": "\n".join(page),
        "offset": offset,
        "limit": requested_limit,
        "first_line": offset if returned else None,
        "last_line": last_line,
        "total_lines": total_lines,
        "total_bytes": st.st_size,
        "truncated": truncated,
        "continue_at": continue_at,
    }
    if cut_line:
        result["line_truncated"] = True
        result["note"] = (
            "The last line alone exceeds the per-call character budget and "
            "was cut; the continuation starts at the next line."
        )
    return result


def build_read_file_tool(roots: list[str] | str | None = None) -> list:
    """Return the built-in host ``read_file`` tool, or ``[]`` if unconfigured.

    Args:
        roots: Workspace-root allowlist.  ``None`` (the runner's case) reads
            ``WORKSPACE_ROOTS`` from the environment; a list/string is for
            tests and explicit wiring.  With no existing directory the tool
            is not registered at all.

    Returns:
        A one-element list containing the MAF ``read_file`` tool.
    """
    workspace_roots = _parse_roots(roots)
    if not workspace_roots:
        return []

    @tool
    async def read_file(
        path: str,
        offset: int = 1,
        limit: int | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        """Read a text file that lives on the hub host, one page at a time.

        Use this for files on the server — a repository prompt, a CSV under
        ``data/``, a generated report — instead of a shell or MCP command.
        It decodes explicitly, numbers every line, and never drops the middle
        of a page, so a large file is paged with ``continue_at`` instead of
        guesswork.  Uploaded documents are different: read those with
        ``read_file_content(file_id=...)``.

        Only paths inside the configured workspace roots are readable.
        Paths outside them, ``..`` traversal, symbolic links and directories
        return a named ``error_code``.

        Args:
            path: Absolute path, or a path relative to a workspace root.
            offset: 1-based line number to start at (default 1).
            limit: Maximum lines to return (default and maximum per call 600).
                A larger value is accepted but still budgeted, and the
                response then carries ``continue_at``.
            encoding: Text encoding (default ``utf-8``).  Pass ``cp1252`` or
                ``latin-1`` only for a legacy file that is not UTF-8.

        Returns a dict with ``path``, ``encoding``, ``text`` (each line
        prefixed ``N: ``), ``offset``, ``limit``, ``first_line``,
        ``last_line``, ``total_lines``, ``total_bytes``, ``truncated`` and
        ``continue_at`` (the next line to request, ``None`` at end of file),
        or ``error``/``error_code`` on failure.
        """
        return _read_file_page(
            path,
            offset=offset,
            limit=limit,
            encoding=encoding,
            roots=workspace_roots,
        )

    return [read_file]
