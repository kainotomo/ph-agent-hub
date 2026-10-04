# =============================================================================
# PH Agent Hub — Subagent Delegate Tool Builder
# =============================================================================
# Builds the MAF FunctionTool that lets a parent chat agent delegate a task
# to a sub-agent.  The child's tool set is the parent session's already-resolved
# pool minus delegated tools, minus an operator deny list, minus approval-gated
# tools.
#
# Issue #574.  One level of nesting only: every delegate callable is removed
# from the child pool, so a child can never delegate further.
# =============================================================================

import json
import logging
from typing import Any

from agent_framework import FunctionInvocationContext, FunctionTool

from ..agents.subagent_bus import get_bus
from ..core.config import settings
from ..db.orm.tools import Tool

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_ROLE = "@general"

#: Cap on the output text carried by ``subagent_tool_result`` events.
_MAX_EVENT_OUTPUT_CHARS = 2000

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": "The complete, self-contained task for the sub-agent.",
        }
    },
    "required": ["task"],
    "additionalProperties": False,
}


def _slugify(value: str) -> str:
    """Return an ASCII-safe lowercase slug for *value*.

    Only ``a-z`` and ``0-9`` survive; every other character becomes ``_``.
    Runs of ``_`` collapse, the result is trimmed and truncated to 40
    characters, and an empty result falls back to ``"subagent"``.
    """
    chars: list[str] = []
    for ch in value:
        low = ch.lower()
        if ("a" <= low <= "z") or ("0" <= ch <= "9"):
            chars.append(low)
        else:
            chars.append("_")
    slug = "_".join(filter(None, "".join(chars).split("_")))[:40]
    return slug or "subagent"


def build_delegate_tool_name(
    tool_name: str, tool_id: str, used_names: set[str] | None = None
) -> str:
    """Return a unique MAF tool name for a subagent row's delegate tool.

    Prefers ``delegate_to_<slug>``; on collision appends the first six
    characters of the row id, then ``_2``, ``_3``, … until free.
    """
    base = "delegate_to_" + _slugify(tool_name)
    if used_names is None or base not in used_names:
        return base
    candidate = f"{base}_{_slugify(tool_id)[:6]}"
    if candidate not in used_names:
        return candidate
    n = 2
    while f"{candidate}_{n}" in used_names:
        n += 1
    return f"{candidate}_{n}"


def _build_child_pool(pool: list, deny: Any) -> tuple[list, list]:
    """Split *pool* into the child's tool list and the omitted tool names.

    Delegated tools are dropped silently (nesting is capped at one level).
    Operator-denied and approval-gated tools are dropped and recorded so the
    transcript can explain what the child did not receive.
    """
    deny_set = {d for d in (deny or []) if isinstance(d, str) and d}
    child: list = []
    omitted: list = []
    for t in pool:
        name = getattr(t, "name", "") or ""
        if getattr(t, "_delegate_tool", False):
            continue
        if name in deny_set:
            omitted.append(name)
            continue
        if getattr(t, "approval_mode", "never_require") == "always_require":
            omitted.append(name)
            continue
        child.append(t)
    return child, omitted


def _emit(bus: Any, event_name: str, payload: dict) -> None:
    """Queue a ``subagent_*`` event; a no-op when no bus is registered."""
    if bus is None:
        return
    bus.put_nowait({"event": event_name, "data": json.dumps(payload)})


def _stringify_output(output: Any) -> str:
    """Render a child tool output for an SSE event, capped for transport."""
    if output is None:
        return ""
    text = output if isinstance(output, str) else repr(output)
    if len(text) > _MAX_EVENT_OUTPUT_CHARS:
        return text[:_MAX_EVENT_OUTPUT_CHARS]
    return text


def _is_telemetry_context_error(exc: BaseException) -> bool:
    """Detect MAF's telemetry ``ContextVar`` cross-context cleanup artifact.

    ``agent_framework.observability`` performs
    ``INNER_RESPONSE_TELEMETRY_CAPTURED_FIELDS.set(...)`` when a streaming run
    starts, then ``.reset(token)`` inside its ``_finalize_stream()``.  A reset
    only succeeds in the context that created the token, so if finalisation runs
    in a task that copied the context (or after the stream is closed from
    elsewhere) ``ContextVar.reset`` raises
    ``ValueError: <Token ...> was created in a different Context``.

    This is a teardown artifact, not a failure of the delegated work.  The
    parent stream already tolerates it at stream end (see ``run_agent_stream``);
    the delegated child treats it as end-of-stream for the same reason, and
    keeps whatever text it already accumulated.
    """
    return "inner_response_telemetry_captured_fields" in str(exc)


async def _run_child(
    *,
    tool: Tool,
    delegate_name: str,
    config: dict,
    task: str,
    child_pool: list,
    omitted: list,
    session_id: str,
    tenant_id: str,
    session_data: dict | None,
    parent_call_id: str,
    bus: Any,
    parent_temperature: float,
) -> tuple[str, dict]:
    """Run the delegated child agent, streaming ``subagent_*`` events onto *bus*.

    Returns ``(child_text, usage)`` where ``usage`` is a dict with
    ``tokens_in`` and ``tokens_out`` keys.  A failure that the child itself
    can report (unbound model role, timeout, cancellation, step cap) is
    returned as a JSON error *string* rather than raised, so the parent LLM
    can relay it to the user.
    """
    import asyncio
    import time

    from agent_framework import Agent

    from ..agents.runner import (
        _build_compaction_strategy,
        _build_session_context_block,
        _handle_streaming_function_call,
        _is_tool_error,
        _resolve_tool_arguments,
        _summarise_tool_result,
    )
    from ..core.redis import check_stream_cancel
    from ..db.base import AsyncSessionLocal
    from ..models.base import get_chat_client
    from ..services.model_role_service import resolve_role_model

    role = config.get("model_role") or _DEFAULT_MODEL_ROLE
    persona = getattr(tool, "name", "") or "sub-agent"

    # Resolve the role on a short-lived session: the caller's session is live
    # inside ``agent.run()`` and async sessions are not concurrency-safe.
    async with AsyncSessionLocal() as role_db:
        model = await resolve_role_model(role_db, tenant_id, role)

    if model is None:
        message = (
            f"Subagent model role '{role}' is not bound to an enabled model "
            f"for this tenant. Bind it under Admin -> Model Roles."
        )
        logger.warning("Subagent '%s': %s", persona, message)
        _emit(
            bus,
            "subagent_error",
            {"parent_call_id": parent_call_id, "message": message},
        )
        return json.dumps({"error": message}), {"tokens_in": 0, "tokens_out": 0}

    client = get_chat_client(model, thinking_enabled=False, reasoning_effort=None)

    # --- Child system prompt ------------------------------------------------
    parts: list[str] = []
    instructions = (config.get("instructions") or "").strip()
    if instructions:
        parts.append(instructions)
    if config.get("include_session_context", True) and session_data:
        try:
            block = _build_session_context_block(session_data)
        except Exception:
            logger.debug("Session context block failed for subagent", exc_info=True)
            block = None
        if block:
            parts.append(block)
    parts.append(f"## Task\n{task}")
    full_instructions = "\n\n".join(parts)

    # --- Child run options --------------------------------------------------
    temperature = config.get("temperature")
    if temperature is None:
        temperature = parent_temperature
    default_options: dict = {"temperature": float(temperature)}
    if settings.AGENT_PARALLEL_TOOLS_ENABLED and child_pool:
        default_options["allow_multiple_tool_calls"] = True
    if getattr(model, "max_tokens", 0) and model.max_tokens > 0:
        default_options["max_tokens"] = model.max_tokens

    compaction_strategy, tokenizer = _build_compaction_strategy(
        model, client, child_pool,
    )

    agent = Agent(
        client=client,
        name=_slugify(persona),
        instructions=full_instructions,
        tools=child_pool,
        default_options=default_options,
        compaction_strategy=compaction_strategy,
        tokenizer=tokenizer,
    )

    configured_timeout = config.get("timeout_s")
    timeout_s = (
        int(configured_timeout)
        if configured_timeout is not None
        else int(settings.SUBAGENT_TIMEOUT)
    )
    max_steps = int(settings.SUBAGENT_MAX_STEPS)

    _emit(
        bus,
        "subagent_start",
        {
            "parent_call_id": parent_call_id,
            "name": persona,
            "tool_name": delegate_name,
            "prompt": task,
            "model_role": role,
            "model_name": getattr(model, "name", "") or "",
            "tools": [getattr(t, "name", "") for t in child_pool],
            "omitted": list(omitted),
            "depth": 0,
        },
    )

    # --- Stream the child ---------------------------------------------------
    started_at = time.monotonic()
    deadline = started_at + timeout_s
    updates_seen = 0
    step_count = 0
    text_parts: list[str] = []
    pending_calls: dict[str, dict] = {}
    error_message: str | None = None

    stream = agent.run(task, stream=True)
    iterator = stream.__aiter__()

    try:
        while True:
            # Cheap Redis check amortised over several updates — the child can
            # emit token deltas far faster than the parent's event cadence.
            if updates_seen % 20 == 0 and await check_stream_cancel(session_id):
                error_message = "Subagent cancelled with the session."
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error_message = f"Subagent exceeded the {timeout_s}s time limit."
                break

            try:
                update = await asyncio.wait_for(
                    iterator.__anext__(), timeout=remaining,
                )
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                error_message = f"Subagent exceeded the {timeout_s}s time limit."
                break
            except Exception as exc:
                if _is_telemetry_context_error(exc):
                    # See _is_telemetry_context_error: MAF's streaming
                    # middleware resets a ContextVar token from a context it
                    # was not created in.  A cleanup artifact, not a failure.
                    logger.debug(
                        "Subagent stream ended on telemetry ContextVar cleanup",
                        exc_info=True,
                    )
                    break
                raise

            updates_seen += 1

            for content in getattr(update, "contents", None) or []:
                ctype = getattr(content, "type", None)

                if ctype == "text":
                    delta = getattr(content, "text", "") or ""
                    if delta:
                        text_parts.append(delta)
                        _emit(
                            bus,
                            "subagent_token",
                            {"parent_call_id": parent_call_id, "delta": delta},
                        )
                elif ctype == "text_reasoning":
                    delta = getattr(content, "text", "") or ""
                    if delta:
                        _emit(
                            bus,
                            "subagent_reasoning_token",
                            {"parent_call_id": parent_call_id, "delta": delta},
                        )
                elif ctype in ("function_call", "tool_call"):
                    _handle_streaming_function_call(content, pending_calls)
                elif ctype in ("function_result", "tool_result"):
                    call_id = getattr(content, "call_id", None) or ""
                    tool_call_id = call_id or f"step-{step_count}"
                    pending = pending_calls.pop(call_id, None) or {}
                    tool_name = pending.get("name") or getattr(
                        content, "name", "unknown"
                    )
                    output = getattr(content, "output", None)
                    if output is None:
                        output = getattr(content, "result", None)

                    _emit(
                        bus,
                        "subagent_tool_start",
                        {
                            "parent_call_id": parent_call_id,
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "arguments": _resolve_tool_arguments(
                                pending.get("args_str"), output,
                            ),
                        },
                    )
                    _emit(
                        bus,
                        "subagent_tool_result",
                        {
                            "parent_call_id": parent_call_id,
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "success": not _is_tool_error(output),
                            "result_summary": _summarise_tool_result(output),
                            "output": _stringify_output(output),
                        },
                    )
                    step_count += 1
                elif ctype == "function_approval_request":
                    # Approval-gated tools are excluded from the child pool, so
                    # this should be unreachable; fail loudly rather than hang.
                    error_message = (
                        "Subagent requested approval, which is not supported."
                    )
                    break

            if error_message:
                break
            if step_count >= max_steps:
                error_message = (
                    f"Subagent stopped after {max_steps} tool rounds."
                )
                break
    finally:
        # Release the child stream's resources even on timeout/cancel paths.
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception:
                logger.debug("Subagent stream aclose failed", exc_info=True)

    child_text = "".join(text_parts).strip()
    tokens_in = 0
    tokens_out = 0

    try:
        final = getattr(stream, "_final_result", None)
        if final is None:
            final = await stream.get_final_response()
        if final is not None:
            # The final response is authoritative: if the stream was cut short
            # by the telemetry teardown artifact above, the accumulated deltas
            # may be incomplete.
            final_text = (getattr(final, "text", None) or "").strip()
            if final_text:
                child_text = final_text
            usage = getattr(final, "usage_details", None)
            if isinstance(usage, dict):
                tokens_in = usage.get("input_token_count", 0) or 0
                tokens_out = usage.get("output_token_count", 0) or 0
    except Exception:
        logger.debug("Subagent token extraction failed", exc_info=True)

    usage_out = {"tokens_in": tokens_in, "tokens_out": tokens_out}
    duration_ms = int((time.monotonic() - started_at) * 1000)

    if error_message:
        _emit(
            bus,
            "subagent_error",
            {"parent_call_id": parent_call_id, "message": error_message},
        )
        return json.dumps({"error": error_message}), usage_out

    _emit(
        bus,
        "subagent_complete",
        {
            "parent_call_id": parent_call_id,
            "success": True,
            "duration_ms": duration_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "steps": step_count,
        },
    )
    return child_text or "(sub-agent returned no output)", usage_out


def build_subagent_delegate(
    *,
    tool: Tool,
    tenant_id: str,
    session_id: str,
    pool: list,
    session_data: dict | None = None,
    parent_temperature: float = 0.7,
    used_names: set[str] | None = None,
) -> FunctionTool:
    """Build a MAF FunctionTool that delegates a task to a sub-agent.

    The child's tool set is the parent session's already-resolved *pool* minus
    delegated tools, minus an operator deny list, minus approval-gated tools.
    """
    name = build_delegate_tool_name(
        getattr(tool, "name", "") or "",
        getattr(tool, "id", "") or "",
        used_names,
    )
    if used_names is not None:
        used_names.add(name)

    description = getattr(tool, "description", None) or (
        f"Delegate a task to the {getattr(tool, 'name', 'sub-agent')} sub-agent."
    )
    approval_mode = (
        "always_require"
        if getattr(tool, "approval_required", False)
        else "never_require"
    )

    async def _delegate(ctx: FunctionInvocationContext, task: str) -> str:
        """Delegate *task* to this sub-agent and return its final answer.

        The sub-agent runs in its own context with the tools resolved for
        this session.  Use it to offload focused work that would otherwise
        consume this conversation's context.
        """
        config = dict(tool.config or {})
        parent_call_id = str((ctx.metadata or {}).get("call_id") or "")
        child_pool, omitted = _build_child_pool(pool, config.get("tool_deny"))
        bus = get_bus(session_id)
        try:
            text, usage = await _run_child(
                tool=tool,
                delegate_name=name,
                config=config,
                task=task,
                child_pool=child_pool,
                omitted=omitted,
                session_id=session_id,
                tenant_id=tenant_id,
                session_data=session_data,
                parent_call_id=parent_call_id,
                bus=bus,
                parent_temperature=parent_temperature,
            )
        except Exception as exc:
            logger.warning(
                "Subagent '%s' failed: %s",
                getattr(tool, "name", "?"),
                exc,
                exc_info=True,
            )
            _emit(
                bus,
                "subagent_error",
                {"parent_call_id": parent_call_id, "message": str(exc)},
            )
            return json.dumps({"error": f"Subagent failed: {exc}"})

        if bus is not None and isinstance(usage, dict):
            bus.add_usage(usage.get("tokens_in"), usage.get("tokens_out"))
        return text or ""

    tool_obj = FunctionTool(
        name=name,
        description=description,
        func=_delegate,
        input_model=_INPUT_SCHEMA,
        approval_mode=approval_mode,
    )
    tool_obj._delegate_tool = True
    tool_obj._subagent_row_id = getattr(tool, "id", "")
    #: Outer output-cap wrapper uses this so the child's own timeout fires
    #: first and can report a clean error instead of a generic tool timeout.
    configured_timeout = (tool.config or {}).get("timeout_s")
    tool_obj._subagent_timeout_s = (
        int(configured_timeout)
        if configured_timeout is not None
        else int(settings.SUBAGENT_TIMEOUT)
    )
    return tool_obj
