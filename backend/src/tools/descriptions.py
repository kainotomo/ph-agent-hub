# =============================================================================
# PH Agent Hub — Tool Capabilities Resolver
# =============================================================================
# AST-based extraction of tool capabilities from Python source files.
#
# Reads *sibling* .py modules from the tools package directory, parses each
# into an AST, walks all @tool-decorated functions, and returns their
# docstrings as capability strings.
#
# This module never imports builder functions (they need DB/network/playwright)
# — it is pure static analysis using only the `ast` and `pathlib` stdlib.
# =============================================================================

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def type_capabilities(tool_type: str) -> list[str]:
    """Return the derived capabilities for a built-in tool type.

    Capabilities are extracted from the docstrings of @tool-decorated
    functions in the sibling module ``<tool_type>.py``.

    Args:
        tool_type: e.g. ``"calculator"``, ``"erpnext"``, ``"sql_query"``.

    Returns:
        A list of capability strings (one per tool).  Returns ``[]``
        when the module does not exist or no valid tools are found.
    """
    module_tools = _load_module_tool_docstrings(tool_type)
    if module_tools is None:
        return []
    return list(module_tools)


def custom_capabilities(code: str) -> list[str]:
    """Derive capabilities from raw Python code for a custom tool.

    Args:
        code: The full Python source of the custom tool module.

    Returns:
        A list of capability strings (one per @tool-decorated function
        with a valid docstring).
    """
    return list(_parse_code_docstrings(code))


def resolve_capabilities(
    tool_type: str,
    code: Optional[str] = None,
) -> list[str]:
    """Resolve capabilities from a tool type (and optional custom code).

    When ``code`` is provided (custom tools) it is used directly.
    Otherwise the sibling module for ``tool_type`` is loaded.

    Returns an empty list when nothing can be derived.
    """
    if code is not None:
        return custom_capabilities(code)
    return type_capabilities(tool_type)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

_TOOLS_DIR = Path(__file__).parent


@functools.lru_cache(maxsize=128)
def _load_module_tool_docstrings(tool_type: str) -> tuple[str, ...] | None:
    """AST-parse ``<tool_type>.py`` and return deduped capability strings.

    Returns ``None`` when the module cannot be found.
    The result is cached so that repeated calls are fast dict-lookups.
    """
    path = _TOOLS_DIR / f"{tool_type}.py"
    if not path.is_file():
        return None

    source = path.read_text(encoding="utf-8")
    return tuple(_parse_code_docstrings(source))


def _parse_code_docstrings(code: str) -> list[str]:
    """Yield valid capability strings from a Python source file.

    Filters:
      - Only functions decorated with ``@tool`` (Name, Attribute, or Call).
      - Skips functions whose name starts with ``_``.
      - Skips empty docstrings, ``PLACEHOLDER`` markers, and "not configured" lines.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    capabilities: list[str] = []
    seen: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # Check for @tool decoration
        if not _has_tool_decorator(node):
            continue

        # Skip private functions
        if node.name.startswith("_"):
            continue

        # Extract docstring – handles both plain string-literal docstrings
        # and Call-wrapped ones like ``"""doc""".format(...)``.
        docstring = _extract_docstring(node)
        if not docstring:
            continue

        cap = _clean_docstring(docstring)
        if cap and cap not in seen:
            seen.add(cap)
            capabilities.append(cap)

    return capabilities


# Pattern: @tool  or  @tool(...)  or  @agent_framework.tool  etc.
# We track decorator nodes that match these patterns.
_TOOL_DECORATOR_PATTERNS = (
    "_tool_dec_name",       # @tool  → ast.Name(id='tool')
    "_tool_dec_attr",       # @agent_framework.tool  → ast.Attribute(attr='tool')
    "_tool_dec_call",       # @tool(name=...)  → ast.Call(func=Name or Attribute)
)


def _has_tool_decorator(func_node: ast.FunctionDef) -> bool:
    """Return True if *func_node* has a @tool-like decorator.

    Handles three decorator forms:
      1. ``@tool``  (Name)
      2. ``@agent_framework.tool``  (Attribute)
      3. ``@tool(name=...)``  (Call wrapping Name or Attribute)
    """
    for dec in func_node.decorator_list:
        if _is_tool_decorator(dec):
            return True
    return False


def _is_tool_decorator(dec: ast.expr) -> bool:
    """Check a single decorator expression for @tool pattern."""
    # Form 1 & 3: @tool  or  @tool(...)
    if isinstance(dec, ast.Name) and dec.id == "tool":
        return True

    # Form 2: @something.tool  (attribute access ending in 'tool')
    if isinstance(dec, ast.Attribute) and dec.attr == "tool":
        return True

    # Form 3: @tool(...)  (Call wrapping Name or Attribute)
    if isinstance(dec, ast.Call):
        inner = dec.func
        if isinstance(inner, ast.Name) and inner.id == "tool":
            return True
        if isinstance(inner, ast.Attribute) and inner.attr == "tool":
            return True

    return False


# ---------------------------------------------------------------------------
# Docstring extraction & cleaning
# ---------------------------------------------------------------------------


def _extract_docstring(func_node):
    r'''Return the docstring from a function node.

    Handles plain string-literal docstrings and call-wrapped strings
    like "docstring".format(...).

    Returns None when no docstring is found.
    '''
    if not func_node.body:
        return None

    first_stmt = func_node.body[0]
    if not isinstance(first_stmt, ast.Expr):
        return None

    expr = first_stmt.value

    # Case 1: plain string literal  (ast.Constant)
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value

    # Case 2: Call-wrapped string  e.g.  docstring.format(...)
    if isinstance(expr, ast.Call):
        inner = expr.func
        #  docstring.format  -> Attribute(attr='format', value=Constant)
        if (
            isinstance(inner, ast.Attribute)
            and inner.attr == "format"
            and isinstance(inner.value, ast.Constant)
            and isinstance(inner.value.value, str)
        ):
            return inner.value.value

    return None


def _clean_docstring(docstring: str) -> str:
    """Normalise a docstring into a capability string.

    Filters out placeholder / stub / unconfigured markers.
    Returns the first line (or first sentence) as the capability label,
    with the full docstring as the value.
    """
    stripped = docstring.strip()

    if not stripped:
        return ""

    # Filter: PLACEHOLDER
    if "DOCSTRING_PLACEHOLDER" in stripped or "PLACEHOLDER" in stripped:
        return ""

    # Filter: "not configured"
    if re.search(r"not\s+configured", stripped, re.IGNORECASE):
        return ""

    # Filter: "stub" or "deferred"
    if re.search(r"\b(stub|deferred)\b", stripped, re.IGNORECASE):
        return ""

    # Take the first non-empty paragraph as the "headline" capability.
    paragraphs = stripped.split("\n\n")
    first_para = paragraphs[0].strip()

    # If the first line is short enough, use it directly as the capability label.
    # Otherwise use the full docstring.
    first_line = stripped.split("\n")[0].strip()
    if first_line and len(first_line) <= 120:
        # Use first line as concise capability label
        return first_line
    else:
        # Use the first paragraph as the capability label
        return first_para


# ---------------------------------------------------------------------------
# Quick CLI for debugging
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        tool_type = sys.argv[1]
    else:
        tool_type = "calculator"

    caps = type_capabilities(tool_type)
    print(f"=== {tool_type}: {len(caps)} capabilities ===")
    for cap in caps:
        print(f"  - {cap}")
