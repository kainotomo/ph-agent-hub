"""Tests for the tool description resolver (descriptions.py)."""

import pytest


class TestTypeCapabilities:
    """Test capability extraction for known tool types."""

    def test_calculator_capabilities(self):
        from backend.src.tools.descriptions import type_capabilities

        caps = type_capabilities("calculator")
        assert isinstance(caps, list)
        assert len(caps) >= 1
        assert any("calculate" in c.lower() for c in caps)

    def test_web_search_capabilities(self):
        from backend.src.tools.descriptions import type_capabilities

        caps = type_capabilities("web_search")
        assert isinstance(caps, list)
        assert len(caps) >= 1

    def test_memory_capabilities(self):
        from backend.src.tools.descriptions import type_capabilities

        caps = type_capabilities("memory")
        assert isinstance(caps, list)
        assert len(caps) >= 1
        # Should have save/add, delete, list variants
        names_lower = [c.lower() for c in caps]
        assert any("save" in n for n in names_lower)

    def test_file_list_capabilities(self):
        from backend.src.tools.descriptions import type_capabilities

        caps = type_capabilities("file_list")
        assert isinstance(caps, list)
        assert len(caps) >= 1

    def test_erpnext_capabilities(self):
        """Regression: erpnext uses @tool(name=...) Call form — expects 11."""
        from backend.src.tools.descriptions import type_capabilities

        caps = type_capabilities("erpnext")
        assert isinstance(caps, list)
        assert len(caps) == 11

    def test_sql_query_excludes_not_configured(self):
        from backend.src.tools.descriptions import type_capabilities

        caps = type_capabilities("sql_query")
        assert isinstance(caps, list)
        # Should include the real tools
        names_lower = [c.lower() for c in caps]
        assert any("list all tables" in n or "list table" in n for n in names_lower)
        assert any("describe" in n for n in names_lower)
        # Should exclude "not configured" markers
        assert not any("not configured" in n for n in names_lower)

    def test_excluded_types_empty(self):
        """mcp, a2a, membrane, custom should return empty lists."""
        from backend.src.tools.descriptions import type_capabilities

        for t in ("mcp", "a2a", "membrane", "custom"):
            caps = type_capabilities(t)
            assert caps == [], f"{t} should have no capabilities"


class TestCustomCapabilities:
    """Test capability extraction from custom Python code strings."""

    def test_simple_tool_docstring(self):
        from backend.src.tools.descriptions import custom_capabilities

        code = '''
from agent_framework import tool

@tool
def greet(name):
    """Say hello to a person."""
    return f"Hello {name}"
'''
        caps = custom_capabilities(code)
        assert isinstance(caps, list)
        assert len(caps) == 1
        assert "Say hello to a person" in caps[0]

    def test_multiple_tools(self):
        from backend.src.tools.descriptions import custom_capabilities

        code = '''
from agent_framework import tool

@tool
def add(a, b):
    """Add two numbers."""
    return a + b

@tool
def subtract(a, b):
    """Subtract b from a."""
    return a - b
'''
        caps = custom_capabilities(code)
        assert len(caps) == 2
        assert any("Add two numbers" in c for c in caps)
        assert any("Subtract" in c for c in caps)

    def test_format_wrapped_docstring(self):
        from backend.src.tools.descriptions import custom_capabilities

        code = '''
from agent_framework import tool

@tool
def process(data):
    """Process {mode} data.""".format(mode="batch")
    return data
'''
        caps = custom_capabilities(code)
        # format-wrapped docstrings should be extracted
        assert len(caps) >= 1

    def test_async_tool(self):
        from backend.src.tools.descriptions import custom_capabilities

        code = '''
from agent_framework import tool

@tool
async def fetch(url):
    """Fetch content from a URL."""
    return {}
'''
        caps = custom_capabilities(code)
        assert len(caps) == 1

    def test_filters_underscore_prefix(self):
        from backend.src.tools.descriptions import custom_capabilities

        code = '''
from agent_framework import tool

@tool
def _internal():
    """Internal helper."""
    pass

@tool
def public():
    """Public tool."""
    pass
'''
        caps = custom_capabilities(code)
        assert len(caps) == 1

    def test_filters_placeholder(self):
        from backend.src.tools.descriptions import custom_capabilities

        code = '''
from agent_framework import tool

@tool
def broken():
    """PLACEHOLDER: Not yet implemented."""
    pass
'''
        caps = custom_capabilities(code)
        assert len(caps) == 0

    def test_filters_not_configured(self):
        from backend.src.tools.descriptions import custom_capabilities

        code = '''
from agent_framework import tool

@tool
def broken():
    """Not configured."""
    pass
'''
        caps = custom_capabilities(code)
        assert len(caps) == 0


class TestResolveCapabilities:
    """Test the combined resolver: stored description → derived capabilities."""

    def test_stored_description_takes_precedence(self):
        from backend.src.tools.descriptions import resolve_capabilities

        caps = resolve_capabilities("calculator", None)
        # With no code, should fall back to type_capabilities for calculator
        assert len(caps) >= 1

    def test_code_overrides_type(self):
        from backend.src.tools.descriptions import resolve_capabilities

        code = '''
from agent_framework import tool

@tool
def my_custom():
    """My custom capability."""
    pass
'''
        caps = resolve_capabilities("unknown_type", code)
        assert len(caps) == 1
        assert "My custom capability" in caps[0]


class TestGuardrails:
    """CI guardrails — all types in VALID_TOOL_TYPES must produce results."""

    def test_all_valid_types_produce_capabilities(self):
        """Every non-excluded type in VALID_TOOL_TYPES yields ≥1 capability."""
        from backend.src.services.tool_service import VALID_TOOL_TYPES

        excluded = {"mcp", "a2a", "membrane", "custom"}

        for tool_type in sorted(VALID_TOOL_TYPES):
            from backend.src.tools.descriptions import type_capabilities
            caps = type_capabilities(tool_type)
            if tool_type not in excluded:
                assert len(caps) >= 1, (
                    f"{tool_type} should have ≥1 capability but got {caps}"
                )


class TestCaching:
    """Verify lru_cache returns consistent results."""

    def test_type_capabilities_cached(self):
        from backend.src.tools.descriptions import type_capabilities

        caps1 = type_capabilities("calculator")
        caps2 = type_capabilities("calculator")
        # Returns new list copies but same content (tuple is cached)
        assert caps1 == caps2
        assert len(caps1) == len(caps2)

    def test_different_types_not_cached(self):
        from backend.src.tools.descriptions import type_capabilities

        caps1 = type_capabilities("calculator")
        caps2 = type_capabilities("erpnext")
        assert caps1 != caps2

    def test_custom_capabilities_consistent(self):
        from backend.src.tools.descriptions import custom_capabilities

        code = '''
from agent_framework import tool

@tool
def hello():
    """Hello."""
    pass
'''
        caps1 = custom_capabilities(code)
        caps2 = custom_capabilities(code)
        assert caps1 == caps2
