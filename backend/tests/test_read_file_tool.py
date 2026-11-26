# =============================================================================
# PH Agent Hub — Host read_file tool tests (Issue #658)
# =============================================================================
# Pure unit tests for ``src.tools.read_file.build_read_file_tool`` — no DB, no
# network, no conftest fixtures.  Every test builds its own workspace root
# from pytest's ``tmp_path`` and calls the built MAF tool's ``.func`` directly.
#
# The properties pinned here are the ones the tool was introduced for:
#   * a large file is paged with ``continue_at`` and the pages reconstruct the
#     file exactly (nothing dropped in the middle);
#   * the per-call character budget wins over an oversized ``limit`` and the
#     response says where to continue;
#   * decoding is explicit (UTF-8 by default, ``cp1252`` on request) instead of
#     the shell's code page;
#   * only regular files inside the configured roots are readable, and every
#     refusal carries a named ``error_code``.
# =============================================================================

from pathlib import Path

import pytest

from src.tools.read_file import (
    READ_FILE_MAX_CHARS,
    READ_FILE_MAX_LINES,
    build_read_file_tool,
)

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lines(count: int = 2063, width: int = 80) -> list[str]:
    """The numbered, fixed-width lines used by the paging tests."""
    return [f"{i:04d} " + "x" * width for i in range(1, count + 1)]


def _write_bytes(root: Path, name: str, data: bytes) -> Path:
    """Write *data* verbatim so byte-level assertions are exact."""
    path = root / name
    path.write_bytes(data)
    return path


def _unprefix(text: str) -> list[str]:
    """Strip the ``N: `` line-number prefix from each rendered line."""
    if not text:
        return []
    return [line.split(": ", 1)[1] for line in text.split("\n")]


def _assert_error(result: dict, code: str) -> None:
    """Assert the tool refused the request with a named error code."""
    assert "error" in result, result
    assert result["error_code"] == code, result
    assert result["error"]


def _tool(root: Path):
    """Build the read_file tool scoped to a single workspace root."""
    tools = build_read_file_tool([str(root)])
    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "read_file"
    return tool


# ---------------------------------------------------------------------------
# Paging — the whole file is reachable and nothing is dropped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_page_read_covers_whole_file(tmp_path):
    """Paging with ``continue_at`` must reconstruct a 2,063-line file exactly."""
    original = _lines()
    data = "\n".join(original).encode("utf-8")
    path = _write_bytes(tmp_path, "big.txt", data)
    tool = _tool(tmp_path)

    pages: list[dict] = []
    collected: list[str] = []
    offset: int | None = 1
    while offset is not None:
        page = await tool.func(path="big.txt", offset=offset, limit=600)
        assert "error" not in page, page
        pages.append(page)
        collected.extend(_unprefix(page["text"]))

        # Every page reports the same file-level facts.
        assert page["total_lines"] == 2063
        assert page["total_bytes"] == path.stat().st_size == len(data)
        assert page["encoding"] == "utf-8"
        assert page["offset"] == offset
        assert page["limit"] == 600
        assert page["first_line"] == offset
        assert len(page["text"]) <= READ_FILE_MAX_CHARS

        offset = page["continue_at"]

    assert len(pages) > 1, "a 2,063-line file must need more than one page"
    assert pages[0]["first_line"] == 1
    assert pages[-1]["last_line"] == 2063
    assert pages[-1]["continue_at"] is None
    assert collected == original

    # Interior pages are cut by the 30,000-character budget before reaching the
    # requested 600 lines, so they are flagged ``truncated`` — but only in the
    # "stopped short of the limit" sense: the continuation starts at the very
    # next line, so no middle content is dropped.
    for page in pages:
        assert isinstance(page["truncated"], bool)
        if page["truncated"]:
            assert page["continue_at"] == page["last_line"] + 1
    assert pages[-1]["truncated"] is False

    # A page small enough to fit the character budget is never truncated.
    offset = 1
    safe_pages = 0
    last_safe: dict = {}
    while offset is not None:
        last_safe = await tool.func(path="big.txt", offset=offset, limit=300)
        assert last_safe["truncated"] is False, last_safe
        if last_safe["continue_at"] is not None:
            assert last_safe["continue_at"] == last_safe["last_line"] + 1
        offset = last_safe["continue_at"]
        safe_pages += 1
    assert safe_pages > 1
    assert last_safe["last_line"] == 2063


@pytest.mark.asyncio
async def test_limit_larger_than_budget_returns_continue_at(tmp_path):
    """``limit=5000`` is accepted but budgeted, and the cut is at the end."""
    original = _lines()
    path = _write_bytes(tmp_path, "big.txt", "\n".join(original).encode("utf-8"))
    tool = _tool(tmp_path)

    result = await tool.func(path="big.txt", offset=1, limit=5000)

    assert "error" not in result, result
    assert result["limit"] == 5000, "the caller's limit is echoed back"
    assert result["total_lines"] == 2063
    assert result["total_bytes"] == path.stat().st_size
    assert result["truncated"] is True
    assert result["continue_at"] == result["last_line"] + 1
    assert result["first_line"] == 1
    assert result["last_line"] < 2063

    # The cut is a clean tail drop: no "..." marker and no missing middle.
    assert "..." not in result["text"]
    assert len(_unprefix(result["text"])) < READ_FILE_MAX_LINES
    assert _unprefix(result["text"]) == original[: result["last_line"]]

    # And the advertised continuation really is the next line.
    rest = await tool.func(
        path="big.txt", offset=result["continue_at"], limit=5000,
    )
    assert rest["first_line"] == result["last_line"] + 1


# ---------------------------------------------------------------------------
# Decoding — explicit, not the shell's code page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_encoding_is_utf8(tmp_path):
    """A UTF-8 file with non-ASCII text decodes as UTF-8 by default."""
    greek = "Καλημέρα — δοκιμή"
    _write_bytes(tmp_path, "greek.txt", greek.encode("utf-8"))
    tool = _tool(tmp_path)

    result = await tool.func(path="greek.txt")

    assert "error" not in result, result
    assert result["encoding"] == "utf-8"
    assert result["text"] == f"1: {greek}"
    assert greek in result["text"]


@pytest.mark.asyncio
async def test_cp1252_reproduces_legacy_mojibake(tmp_path):
    """A cp1252 em dash fails as UTF-8 and decodes when named explicitly."""
    _write_bytes(tmp_path, "legacy.txt", b"\x97")
    tool = _tool(tmp_path)

    default = await tool.func(path="legacy.txt")
    _assert_error(default, "decode_error")
    assert "cp1252" in default["error"], (
        "the error must tell the model which encoding to pass"
    )

    legacy = await tool.func(path="legacy.txt", encoding="cp1252")
    assert "error" not in legacy, legacy
    assert legacy["encoding"] == "cp1252"
    assert "\u2014" in legacy["text"]
    assert legacy["text"] == "1: \u2014"


# ---------------------------------------------------------------------------
# Line handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crlf_and_no_trailing_newline(tmp_path):
    """CRLF is normalised and a missing final newline does not add a line."""
    _write_bytes(tmp_path, "crlf.txt", b"a\r\nb\r\nc")
    tool = _tool(tmp_path)

    result = await tool.func(path="crlf.txt")

    assert "error" not in result, result
    assert result["total_lines"] == 3
    assert result["text"] == "1: a\n2: b\n3: c"
    assert result["first_line"] == 1
    assert result["last_line"] == 3
    assert result["continue_at"] is None
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_line_numbers_and_offsets(tmp_path):
    """An interior page is numbered with the real 1-based line numbers."""
    lines = [f"line {i}" for i in range(1, 21)]
    _write_bytes(tmp_path, "twenty.txt", "\n".join(lines).encode("utf-8"))
    tool = _tool(tmp_path)

    result = await tool.func(path="twenty.txt", offset=11, limit=5)

    assert "error" not in result, result
    assert result["offset"] == 11
    assert result["limit"] == 5
    assert result["first_line"] == 11
    assert result["last_line"] == 15
    assert result["total_lines"] == 20
    assert result["text"].startswith("11: ")
    assert result["text"].endswith(f"15: {lines[14]}")
    assert result["text"] == "\n".join(
        f"{n}: {lines[n - 1]}" for n in range(11, 16)
    )
    assert result["continue_at"] == 16
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_empty_file(tmp_path):
    """An empty file is a 0-line success, not an exception or an error."""
    _write_bytes(tmp_path, "empty.txt", b"")
    tool = _tool(tmp_path)

    result = await tool.func(path="empty.txt", offset=1)

    assert "error" not in result, result
    assert result["total_lines"] == 0
    assert result["total_bytes"] == 0
    assert result["text"] == ""
    assert result["first_line"] is None
    assert result["last_line"] is None
    assert result["continue_at"] is None
    assert result["truncated"] is False


# ---------------------------------------------------------------------------
# Allowlist and path safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_traversal_refused(tmp_path):
    """``..`` is refused by name before it is resolved."""
    _write_bytes(tmp_path, "inside.txt", b"inside")
    tool = _tool(tmp_path)

    result = await tool.func(path="../escape.txt")

    _assert_error(result, "path_traversal")


@pytest.mark.asyncio
async def test_outside_root_refused(tmp_path):
    """An absolute path outside the configured root is refused."""
    outside = tmp_path.parent / f"{tmp_path.name}_outside.txt"
    outside.write_bytes(b"outside the root")
    tool = _tool(tmp_path)
    try:
        result = await tool.func(path=str(outside))
    finally:
        outside.unlink()

    _assert_error(result, "path_not_allowed")


@pytest.mark.asyncio
async def test_symlink_refused(tmp_path):
    """A symlink inside the root is refused even when its target is inside."""
    target = _write_bytes(tmp_path, "target.txt", b"target")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:  # Windows without symlink privileges
        pytest.skip(f"symlinks are not available here: {exc}")

    tool = _tool(tmp_path)
    result = await tool.func(path="link.txt")

    _assert_error(result, "symlink_not_allowed")


@pytest.mark.asyncio
async def test_directory_refused(tmp_path):
    """The root itself resolves but is refused as a non-regular file."""
    _write_bytes(tmp_path, "inside.txt", b"inside")
    tool = _tool(tmp_path)

    result = await tool.func(path=".")

    _assert_error(result, "not_a_regular_file")


@pytest.mark.asyncio
async def test_missing_file(tmp_path):
    """A missing file inside the root is a named error, not an empty page."""
    tool = _tool(tmp_path)

    result = await tool.func(path="missing.txt")

    _assert_error(result, "file_not_found")


@pytest.mark.asyncio
async def test_relative_path_resolves_against_root(tmp_path):
    """A bare filename is resolved against the configured workspace root."""
    _write_bytes(tmp_path, "relative.txt", "relative text".encode("utf-8"))
    tool = _tool(tmp_path)

    result = await tool.func(path="relative.txt")

    assert "error" not in result, result
    assert result["text"] == "1: relative text"
    assert result["path"] == str(Path(tmp_path).resolve() / "relative.txt")


def test_no_roots_disables_tool():
    """With no existing workspace root the tool is not registered at all."""
    assert build_read_file_tool([]) == []


@pytest.mark.asyncio
async def test_invalid_offset(tmp_path):
    """``offset`` is 1-based; 0 is refused with a named error."""
    _write_bytes(tmp_path, "tiny.txt", b"a")
    tool = _tool(tmp_path)

    result = await tool.func(path="tiny.txt", offset=0)

    _assert_error(result, "invalid_offset")
