# =============================================================================
# PH Agent Hub — Subagent Stream Context Tests (Issue #574)
# =============================================================================
# MAF's streaming middleware sets INNER_RESPONSE_TELEMETRY_CAPTURED_FIELDS and
# resets that token inside its ``_finalize_stream()``.  ``ContextVar.reset`` is
# only valid in the context that created the token, so when finalisation runs
# in a task that copied the context it raises
# "was created in a different Context" — which used to fail the whole
# delegation.  These tests pin the mechanism and the detector.
# =============================================================================

import asyncio
import contextvars

import pytest

from src.tools.subagent import _is_telemetry_context_error

pytestmark = [pytest.mark.unit]

_REAL_MESSAGE = (
    "<Token var=<ContextVar name='inner_response_telemetry_captured_fields'"
    " default=None at 0x73a45f5ed8f0> at 0x73a442d8c780>"
    " was created in a different Context"
)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


def test_detects_the_real_message():
    assert _is_telemetry_context_error(ValueError(_REAL_MESSAGE)) is True


def test_detects_message_embedded_in_larger_text():
    wrapped = RuntimeError(f"Subagent failed: {_REAL_MESSAGE}")
    assert _is_telemetry_context_error(wrapped) is True


def test_ignores_unrelated_errors():
    assert _is_telemetry_context_error(RuntimeError("boom")) is False
    assert _is_telemetry_context_error(ValueError("")) is False
    assert _is_telemetry_context_error(TimeoutError()) is False
    assert _is_telemetry_context_error(StopAsyncIteration()) is False


# ---------------------------------------------------------------------------
# Mechanism: why the reset can fail
# ---------------------------------------------------------------------------


def _telemetry_var():
    return contextvars.ContextVar("inner_response_telemetry_captured_fields")


async def test_reset_in_the_creating_context_succeeds():
    var = _telemetry_var()
    token = var.set({"fields"})
    var.reset(token)
    assert var.get(None) is None


async def test_reset_in_a_task_that_copied_the_context_fails():
    """A Task copies the context, so a token made outside is invalid inside.

    This is exactly the shape of MAF's failure: set() happens in the caller,
    reset() runs later from a task/context that did not create the token.
    """
    var = _telemetry_var()
    token = var.set({"fields"})

    async def _reset_in_copied_context():
        var.reset(token)

    with pytest.raises(ValueError, match="different Context"):
        await asyncio.create_task(_reset_in_copied_context())

    var.reset(token)  # original context can still clean up


async def test_reset_after_context_copy_via_run_in_executor_shape():
    """Same failure via an explicitly copied context."""
    var = _telemetry_var()
    token = var.set({"fields"})
    copied = contextvars.copy_context()

    def _reset_inside_copy():
        return copied.run(var.reset, token)

    with pytest.raises(ValueError, match="different Context"):
        _reset_inside_copy()
