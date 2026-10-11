# =============================================================================
# PH Agent Hub — DeepSeek Utility Functions
# =============================================================================
# Lightweight utilities for DeepSeek output processing.
# No longer monkey-patches MAF internals — thinking mode is handled by
# DeepSeekThinkingClient (see src/models/deepseek.py).
#
# Kept: strip_reasoning, extract_json_block (used by stabilizer.py)
# =============================================================================

import re

_RE_THINK = re.compile(
    r"<think[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE
)
_RE_THINK_OPEN = re.compile(
    r"<think[^>]*>", re.IGNORECASE
)
_RE_THINK_CLOSE = re.compile(
    r"</think>", re.IGNORECASE
)


def strip_reasoning(text: str) -> str:
    """Remove DeepSeek ``<think>...</think>`` blocks and reasoning preamble.

    Also handles unclosed ``<think>`` tags by removing everything from the
    opening tag to the end of the text if no closing tag is found.
    """
    # Remove complete <think>...</think> blocks
    text = _RE_THINK.sub("", text)

    # If there's an unclosed <think>, remove from that point onward
    open_match = _RE_THINK_OPEN.search(text)
    close_match = _RE_THINK_CLOSE.search(text)
    if open_match and not close_match:
        text = text[: open_match.start()]

    # Remove leading whitespace/blanks
    text = text.strip()

    return text


def extract_json_block(text: str) -> str:
    """Extract a JSON object or array from mixed text.

    Tries to find the outermost ``{...}`` or ``[...]`` block in *text*.
    Returns the raw matched substring, or the original text if no JSON-like
    structure is found.
    """
    # Try to find the first { and matching }
    start = text.find("{")
    if start == -1:
        start = text.find("[")
    if start == -1:
        return text

    # Bracket matching
    bracket_map = {"{": "}", "[": "]"}
    open_char = text[start]
    close_char = bracket_map[open_char]
    depth = 0
    end = start

    for i in range(start, len(text)):
        ch = text[i]
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if depth == 0:
        return text[start:end]

    return text


# ---------------------------------------------------------------------------
# DSML / leaked tool-call markup sanitising (Issue #579)
# ---------------------------------------------------------------------------
# DeepSeek's thinking models emit tool calls using native DSML special tokens.
# When the OpenAI-compatible endpoint fails to parse them, the markup leaks
# into the model's *text* output -- either as the bare sentinel ``|DSML|`` or
# as tags like ``<|tool_calls|>`` / ``<invoke ...>`` where the delimiter is the
# FULLWIDTH VERTICAL LINE (U+FF5C), not ASCII ``<``.
#
# The old ``_strip_raw_tool_xml`` helper in the runner only matched ASCII
# ``<...tool_calls...>`` tags, so these variants reached the UI and the
# database.  These helpers are the single source of truth for that cleanup.

# Fullwidth vertical line (｜) and ASCII pipe, both used as DSML delimiters.
_PIPE = r"[\uff5c|]"
# A tag whose name denotes tool-call markup (optionally pipe-wrapped).
_TOOL_TAG_NAME = (
    r"[\w_-]*(?:tool_calls|function_calls|tool_call|function_call|invoke|parameter|DSML)[\w_-]*"
)

# A whole ``<...tool_calls...> ... </...tool_calls...>`` container.  Nested
# markup left behind is mopped up by the tag pattern below.
_RE_TOOLCALL_BLOCK = re.compile(
    r"<\s*" + _PIPE + r"?\s*" + _TOOL_TAG_NAME + r"\s*" + _PIPE + r"?\s*[^>]*>"
    r".*?"
    r"</\s*" + _PIPE + r"?\s*" + _TOOL_TAG_NAME + r"\s*" + _PIPE + r"?\s*[^>]*>",
    re.DOTALL | re.IGNORECASE,
)

# Any single opening/closing tool-call tag, including ``<invoke name="...">``,
# ``<parameter ...>`` and pipe-wrapped closers like ``</｜tool_calls｜>``.
_RE_TOOLCALL_TAG = re.compile(
    r"</?\s*" + _PIPE + r"?\s*" + _TOOL_TAG_NAME + r"\s*" + _PIPE + r"?\s*[^>]*>",
    re.IGNORECASE,
)

# Container open/close used to detect an unclosed (truncated) block.
_RE_CONTAINER_OPEN = re.compile(
    r"<\s*" + _PIPE + r"?\s*[\w_-]*(?:tool_calls|function_calls)[\w_-]*\s*"
    + _PIPE + r"?\s*[^>]*>",
    re.IGNORECASE,
)
_RE_CONTAINER_CLOSE = re.compile(
    r"</\s*" + _PIPE + r"?\s*[\w_-]*(?:tool_calls|function_calls)[\w_-]*\s*"
    + _PIPE + r"?\s*[^>]*>",
    re.IGNORECASE,
)

# The bare DSML sentinel, e.g. ``|｜DSML｜|`` (1-2 pipes per side).
_RE_DSML_SENTINEL = re.compile(
    r"[\uff5c|]{1,2}\s*DSML\s*[\uff5c|]{1,2}",
    re.IGNORECASE,
)


def contains_dsml(text: str) -> bool:
    """Return True if *text* contains leaked DeepSeek tool-call markup."""
    if not text:
        return False
    return bool(
        _RE_TOOLCALL_BLOCK.search(text)
        or _RE_TOOLCALL_TAG.search(text)
        or _RE_DSML_SENTINEL.search(text)
    )


def strip_dsml_markers(text: str) -> str:
    """Remove leaked DeepSeek DSML / tool-call markup from *text*.

    Handles fullwidth-pipe variants (``|｜DSML｜|``, ``<｜tool_calls｜>``),
    ASCII tag variants (``<function_calls>``, ``<tool_calls>``, ``<invoke ...>``)
    and unclosed/truncated blocks.  Surrounding whitespace is trimmed only when
    a marker was actually removed, so plain text passes through unchanged.
    """
    if not text:
        return text

    # 1. Drop an unclosed container (truncated stream) from its open tag onward.
    open_match = _RE_CONTAINER_OPEN.search(text)
    if open_match is not None and not _RE_CONTAINER_CLOSE.search(text, open_match.end()):
        text = text[: open_match.start()]

    # 2. Remove complete blocks, then any residual individual tags.
    text = _RE_TOOLCALL_BLOCK.sub("", text)
    text = _RE_TOOLCALL_TAG.sub("", text)

    # 3. Remove the bare sentinel.
    text = _RE_DSML_SENTINEL.sub("", text)

    return text.strip()
