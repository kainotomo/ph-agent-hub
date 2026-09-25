# =============================================================================
# PH Agent Hub — Workflow Engine
# =============================================================================
# Builds and executes MAF 1.19.0 workflows from ``WorkflowDefinition`` objects.
#
# Public API:
#   ``build_workflow(defn, db_session, extra_tools)`` → built ``Workflow`` instance
#   ``run_workflow(workflow, message, **kwargs)``    → ``tuple[str, WorkflowRunResult]``
#   ``iter_workflow_sse(workflow, message, ...)``    → ``AsyncIterator[dict]`` SSE events
#   ``resolve_model(db, key)``                      → ``Model`` from DB
#   ``load_workflow_definition(mod)``               → ``WorkflowDefinition`` from module
# =============================================================================

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Any, AsyncIterator

from agent_framework import (
    Agent,
    AgentExecutor,
    CharacterEstimatorTokenizer,
    ToolResultCompactionStrategy,
    TokenBudgetComposedStrategy,
    Workflow,
    WorkflowBuilder,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions import NotFoundError, ValidationError
from ...db.orm.models import Model
from .definition import WorkflowDefinition
from .executors import StepAgent
from .roles import is_role_reference

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registered-agent helpers
# ---------------------------------------------------------------------------


def _registered_agent_module(agent_ref: str) -> Any:
    """Return the registered agent module for *agent_ref*, or ``None`` if not
    found.  The import is local so the lookup can be patched at
    ``src.agents.registry.get_registered_agent``.
    """
    from ..registry import get_registered_agent

    return get_registered_agent(agent_ref)


def _validate_agent_refs(defn: WorkflowDefinition) -> None:
    """Raise ``ValidationError`` if any ``agent`` step references a key that
    is not present in the registered-agent store.
    """
    for step in defn.steps:
        if step.type == "agent":
            mod = _registered_agent_module(step.agent_ref)  # type: ignore[union-attr]
            if mod is None:
                raise ValidationError(
                    f"Workflow '{defn.key}': step '{step.id}' "
                    f"references unknown registered agent '{step.agent_ref}'"
                )


# ---------------------------------------------------------------------------
# Model / client resolution
# ---------------------------------------------------------------------------


async def resolve_model(db: AsyncSession, model_ref: str) -> Model:
    """Look up a ``Model`` record by its ``id`` or ``model_id`` attribute.

    Raises ``NotFoundError`` if not found.
    """
    from sqlalchemy import select

    result = await db.execute(
        select(Model).where(
            (Model.id == model_ref) | (Model.model_id == model_ref)
        )
    )
    model = result.scalar_one_or_none()
    if model is None:
        raise NotFoundError(
            f"Model not found for reference '{model_ref}'"
        )
    return model


# ---------------------------------------------------------------------------
# Workflow definition loader
# ---------------------------------------------------------------------------


def load_workflow_definition(mod: Any) -> WorkflowDefinition:
    """Extract a ``WorkflowDefinition`` from a registered MAF module.

    The module must expose either:
      - ``WORKFLOW_DEFINITION``: a ``WorkflowDefinition`` instance or a dict that
        can be parsed into one, OR
      - ``STEPS``: a list of step dicts that get wrapped into a
        ``WorkflowDefinition`` with the module's ``MAF_KEY`` as the workflow
        ``key``.

    Returns:
        A validated ``WorkflowDefinition``.

    Raises:
        ValidationError: If the module has no workflow definition structure.
    """
    if mod is None:
        raise ValidationError("Workflow module is None")

    # Try WORKFLOW_DEFINITION first
    wf_def = getattr(mod, "WORKFLOW_DEFINITION", None)
    if wf_def is not None:
        if isinstance(wf_def, dict):
            defn = WorkflowDefinition(**wf_def)
        elif isinstance(wf_def, WorkflowDefinition):
            defn = wf_def
        else:
            raise ValidationError(
                "WORKFLOW_DEFINITION must be a dict or WorkflowDefinition instance"
            )
        _validate_agent_refs(defn)
        return defn

    # Try STEPS as a shortcut (auto-wrapped into a WorkflowDefinition)
    steps = getattr(mod, "STEPS", None)
    if steps is not None and isinstance(steps, list):
        # Use MAF_KEY as the workflow key
        key = getattr(mod, "MAF_KEY", None)
        if key is None:
            raise ValidationError(
                "Workflow module must have MAF_KEY when defining STEPS"
            )
        name = getattr(mod, "NAME", key)
        description = getattr(mod, "DESCRIPTION", "")
        defn = WorkflowDefinition(
            key=key,
            name=name,
            description=description,
            steps=steps,
        )
        _validate_agent_refs(defn)
        return defn

    raise ValidationError(
        f"Workflow module '{getattr(mod, '__name__', 'unknown')}' has no "
        "WORKFLOW_DEFINITION or STEPS attribute"
    )


# ---------------------------------------------------------------------------
# Agent builder for workflow steps
# ---------------------------------------------------------------------------


def _build_agent_for_step(
    step: Any,  # WorkflowStep
    model: Model,
    tools: list | None,
    temperature: float = 0.7,
    reasoning_effort: str | None = None,
    instructions: str | None = None,
) -> Agent:
    """Create a MAF ``Agent`` for a workflow step.

    Args:
        step:             WorkflowStep with instructions, name, etc.
        model:            Model record for LLM client creation.
        tools:            Optional list of tool callables.
        temperature:      Model temperature.
        reasoning_effort: Optional CoT effort override.
        instructions:     Explicit instructions to use (falls back to
                          ``step.instructions`` when ``None``).

    Returns:
        A configured MAF Agent.
    """
    from agent_framework import Agent, ToolResultCompactionStrategy, CharacterEstimatorTokenizer
    from ...models.base import get_chat_client

    # Build default options
    default_options: dict[str, Any] = {}
    if temperature is not None:
        default_options["temperature"] = temperature
    if reasoning_effort is not None:
        default_options["reasoning_effort"] = reasoning_effort

    # Determine max tokens for compaction
    max_tokens = getattr(model, "max_tokens", None)
    if max_tokens and max_tokens > 0:
        compaction = TokenBudgetComposedStrategy(
            strategies=[ToolResultCompactionStrategy()],
            token_budget=max_tokens,
            tokenizer=CharacterEstimatorTokenizer(),
        )
    else:
        compaction = ToolResultCompactionStrategy()

    agent = Agent(
        client=get_chat_client(model, thinking_enabled=False),
        name=step.name or step.id,
        instructions=instructions if instructions is not None else (step.instructions or ""),
        tools=tools or None,
        default_options=default_options,
        compaction_strategy=compaction,
        tokenizer=CharacterEstimatorTokenizer(),
    )
    return agent


# ---------------------------------------------------------------------------
# Workflow builder
# ---------------------------------------------------------------------------


async def build_workflow(
    defn: WorkflowDefinition,
    db: AsyncSession,
    extra_tools: list | None = None,
    base_temperature: float = 0.7,
    base_reasoning_effort: str | None = None,
    default_model_id: str | None = None,
) -> Workflow:
    """Build a MAF ``Workflow`` from a ``WorkflowDefinition``.

    Steps are connected sequentially via ``WorkflowBuilder.add_chain``.
    The final step's output becomes the workflow output.

    Args:
        defn:                  Workflow definition with steps.
        db:                    Database session (used to resolve Model records).
        extra_tools:           Optional global tools injected into every step.
        base_temperature:      Base temperature for all steps (overridden by step).
        base_reasoning_effort: Base reasoning effort for all steps (overridden by step).
        default_model_id:      Fallback model id to use when a step's model_ref is not
                               found in the DB (typically the skill's default model).

    Returns:
        A built ``Workflow`` instance ready for execution.

    Raises:
        ValidationError: If step configuration is invalid.
    """
    # Build steps into agents + executors
    executors: list[AgentExecutor] = []
    shared: dict[str, Any] = {}

    for i, step in enumerate(defn.steps):
        # Resolve the effective step configuration from the agent module
        # when step.type == "agent".
        instructions = step.instructions
        model_ref = step.model_ref

        if step.type == "agent":
            agent_mod = _registered_agent_module(step.agent_ref)  # type: ignore[union-attr]
            if agent_mod is None:
                raise ValidationError(
                    f"Workflow '{defn.key}': step '{step.id}' "
                    f"references unknown registered agent '{step.agent_ref}'"
                )
            instructions = agent_mod.INSTRUCTIONS
            if step.model_ref is None:
                model_ref = agent_mod.MODEL_ROLE

        # Resolve model for this step (with fallback to skill default)
        model = None
        try:
            model = await resolve_model(db, model_ref)
        except NotFoundError:
            if is_role_reference(model_ref):
                logger.warning(
                    "Step '%s': model role '%s' has no tenant binding (see #550); falling back to '%s'",
                    step.id, model_ref, default_model_id,
                )
            if default_model_id:
                logger.warning(
                    "Step '%s' model_ref '%s' not found, falling back to '%s'",
                    step.id, model_ref, default_model_id,
                )
                model = await resolve_model(db, default_model_id)
            else:
                raise

        # Resolve tools for this step (merge extra tools with step-specific)
        step_tools = list(extra_tools) if extra_tools else []

        # Build agent for this step
        temperature = step.temperature if step.temperature != 0.7 else base_temperature
        reasoning_effort = step.reasoning_effort or base_reasoning_effort

        agent = _build_agent_for_step(
            step=step,
            model=model,
            tools=step_tools,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            instructions=instructions,
        )

        # Apply step-level input semantics without changing topology
        agent = StepAgent(inner=agent, step_id=step.id, input_spec=step.input, shared=shared)

        # Wrap in executor with the step's id as executor id
        executor = AgentExecutor(agent=agent, id=step.id, context_mode=step.context_mode)
        executors.append(executor)

    if not executors:
        raise ValidationError("Workflow must have at least one step")

    # Build the workflow
    builder = WorkflowBuilder(
        name=defn.key,
        description=defn.description,
        start_executor=executors[0],
        output_executors=[executors[-1]],
    )

    # Connect steps sequentially
    if len(executors) > 1:
        builder.add_chain(executors)

    workflow = builder.build()
    logger.info(
        "Built workflow '%s' with %d step(s)",
        defn.key,
        len(executors),
    )
    return workflow


# ---------------------------------------------------------------------------
# Token extraction from workflow results
# ---------------------------------------------------------------------------


def _extract_token_counts_from_workflow(
    result: Any,  # WorkflowRunResult
) -> tuple[int, int, int]:
    """Extract total token counts from a workflow run result.

    Iterates all ``output`` and ``intermediate`` events, accumulates
    ``usage_details`` from ``AgentResponse`` payloads.

    Args:
        result: The WorkflowRunResult from a completed workflow execution.

    Returns:
        (tokens_in, tokens_out, cache_hit_tokens)
    """
    tokens_in = 0
    tokens_out = 0
    cache_hit = 0

    for event in result:
        if event.type not in ("output", "intermediate", "executor_completed"):
            continue
        data = event.data
        if data is None:
            continue
        # For executor_completed, data is a list of AgentExecutorResponse
        if event.type == "executor_completed" and isinstance(data, list):
            for resp in data:
                if hasattr(resp, 'agent_response') and resp.agent_response is not None:
                    agent_resp = resp.agent_response
                    usage = getattr(agent_resp, "usage_details", None)
                    if usage is not None and isinstance(usage, dict):
                        tokens_in += usage.get("input_token_count", 0) or 0
                        tokens_out += usage.get("output_token_count", 0) or 0
                        cache_hit += usage.get("cache_read_input_token_count", 0) or 0
            continue
        # Check if data is an AgentResponse-like object with usage_details
        usage = getattr(data, "usage_details", None)
        if usage is None:
            # Maybe data IS a dict with usage_details
            if isinstance(data, dict) and "usage_details" in data:
                usage = data["usage_details"]
        if usage is None:
            continue
        if isinstance(usage, dict):
            tokens_in += usage.get("input_token_count", 0) or 0
            tokens_out += usage.get("output_token_count", 0) or 0
            cache_hit += usage.get("cache_read_input_token_count", 0) or 0

    return tokens_in, tokens_out, cache_hit


# ---------------------------------------------------------------------------
# Workflow execution (non-streaming)
# ---------------------------------------------------------------------------


async def run_workflow(
    workflow: Workflow,
    message: str,
    function_invocation_kwargs: dict | None = None,
) -> tuple[str, Any]:  # tuple[str, WorkflowRunResult]
    """Execute a workflow synchronously and return (output_text, result).

    Args:
        workflow:                  Built MAF workflow.
        message:                   User message to pass to the workflow.
        function_invocation_kwargs: Optional kwargs forwarded to agent.run().

    Returns:
        Tuple of (final_output_text, WorkflowRunResult).

    Raises:
        ValidationError: If workflow execution fails.
    """
    kwargs: dict[str, Any] = {}
    if function_invocation_kwargs:
        kwargs["function_invocation_kwargs"] = function_invocation_kwargs

    result = await workflow.run(message, **kwargs)

    # Get the final output
    outputs = result.get_outputs()
    if outputs:
        # Combine outputs from all steps
        combined = []
        for o in outputs:
            if isinstance(o, str):
                combined.append(o)
            elif hasattr(o, "text"):
                combined.append(str(o.text))
            elif hasattr(o, "content"):
                combined.append(str(o.content))
            else:
                combined.append(str(o))
        output_text = "\n\n".join(combined)
    else:
        output_text = str(result)

    return output_text, result


# ---------------------------------------------------------------------------
# Streaming: iterate workflow events and yield SSE dicts
# ---------------------------------------------------------------------------


async def iter_workflow_sse(
    workflow: Workflow,
    message: str,
    session_id: str,
    message_id: str,
    token_counts: dict | None = None,
    function_invocation_kwargs: dict | None = None,
    system_prompt: str | None = None,
    tools: list | None = None,
) -> AsyncIterator[dict]:
    """Stream a workflow execution, yielding SSE event dicts per step.

    SSE event types emitted:
        - ``workflow_step``: Per-step lifecycle events
          {event: "workflow_step", data: {workflow_key, step_id, step_index, total_steps, status}}
        - ``token``: Token events from individual steps (delta text)
        - ``tool_start``, ``tool_result``: Tool execution events
        - ``message_complete``: Final completion with token counts and metrics

    Args:
        workflow:                  Built MAF workflow.
        message:                   User message.
        session_id:                Session ID for SSE tracking.
        message_id:                Message ID for SSE tracking.
        token_counts:              Mutable dict for accumulating token counts.
        function_invocation_kwargs: Optional kwargs forwarded to agent.run().
        system_prompt:             System prompt string for token estimation.
        tools:                     List of tool definitions for token estimation.

    Yields:
        SSE event dicts.
    """
    from agent_framework import WorkflowEventType

    # ---- Timing tracking -------------------------------------------------
    turn_start_s = time.monotonic()
    first_token_at_s: float | None = None

    # Get total steps for progress reporting
    total_steps = len(workflow.get_executors_list())

    # Pre-populate step indices from workflow executor list so invoked events have correct step_id
    step_index = 0
    step_tracker: dict[str, int] = {}  # executor_id → step_index
    step_name_by_idx: dict[int, str] = {}  # step_index → executor_name
    current_step_idx = 0  # track which step we're actively streaming for
    next_prepop_idx = 0   # next available pre-populated slot index
    for exe in workflow.get_executors_list():
        # Use 'id' attribute (e.g. "research", "report") — executors don't have 'name'
        exe_id = getattr(exe, 'id', None) or getattr(exe, 'name', None)
        if exe_id:
            if exe_id not in step_tracker:
                step_tracker[exe_id] = step_index
                step_name_by_idx[step_index] = exe_id
                step_index += 1

    # Build kwargs for the stream call
    stream_kwargs: dict[str, Any] = {"stream": True}
    if function_invocation_kwargs:
        stream_kwargs["function_invocation_kwargs"] = function_invocation_kwargs

    # Run the workflow with streaming
    response_stream = workflow.run(message, **stream_kwargs)

    try:
        # Accumulators for token counts during streaming
        streaming_tokens_in = 0
        streaming_tokens_out = 0
        streaming_cache_hit = 0
        event_types_seen = set()
        processed_event_types = set()

        # Iterate over the async stream of WorkflowEvent objects
        async for event in response_stream:
            event_type = event.type
            event_types_seen.add(event_type)
            # Debug: log the first request_info event
            if event_type == "request_info" and event.data is not None:
                data = event.data
                req_attrs = [a for a in dir(data) if not a.startswith("_")]
                logger.info("PH-WORKFLOW-REQUEST_INFO: data_type=%s data_attrs=%s",
                           type(data).__name__, req_attrs)
                # Try to extract token usage
                if hasattr(data, "to_dict"):
                    try:
                        logger.info("PH-WORKFLOW-REQUEST_INFO-dict: %s", json.dumps(str(data.to_dict())[:2000]))
                    except Exception:
                        pass
                elif isinstance(data, dict):
                    logger.info("PH-WORKFLOW-REQUEST_INFO-dict: %s", json.dumps(data)[:2000])
                else:
                    logger.info("PH-WORKFLOW-REQUEST_INFO-str: %s", str(data)[:2000])
            # source_executor_id is only available on certain event types
            # (e.g. request_info); accessing it on others raises ValueError
            try:
                executor_id = getattr(event, "source_executor_id", None) or ""
            except (ValueError, AttributeError, RuntimeError):
                executor_id = ""

            # ---- Step lifecycle events ----
            if event_type == "executor_invoked":
                # Use pre-populated step index from workflow structure
                if executor_id and executor_id in step_tracker:
                    step_num = step_tracker[executor_id]
                elif executor_id:
                    step_tracker[executor_id] = step_index
                    step_num = step_index
                    step_index += 1
                else:
                    # executor_id is empty; use the next pre-populated slot
                    step_num = next_prepop_idx
                    next_prepop_idx += 1
                current_step_idx = step_num
                # Look up actual executor name for step display
                display_step_id = executor_id or step_name_by_idx.get(step_num, executor_id)

                yield {
                    "event": "workflow_step",
                    "data": json.dumps({
                        "step_id": display_step_id,
                        "step_index": step_num,
                        "total_steps": total_steps,
                        "status": "started",
                    }),
                }

            elif event_type == "executor_completed":
                # Extract actual executor_id from response data
                actual_executor_id = executor_id
                step_num = current_step_idx  # should match since executors run sequentially
                if event.data is not None and isinstance(event.data, list) and event.data:
                    first_resp = event.data[0]
                    if hasattr(first_resp, 'executor_id') and first_resp.executor_id:
                        actual_executor_id = first_resp.executor_id
                        # Pre-populated tracker should have this; verify
                        if actual_executor_id not in step_tracker:
                            step_tracker[actual_executor_id] = step_num

                # Extract token counts from AgentExecutorResponse objects in the list
                if event.data is not None and isinstance(event.data, list):
                    for resp in event.data:
                        if hasattr(resp, 'agent_response') and resp.agent_response is not None:
                            agent_resp = resp.agent_response
                            usage = getattr(agent_resp, "usage_details", None)
                            if usage is not None and isinstance(usage, dict):
                                logger.info("PH-WORKFLOW-EXECCOMP-USAGE: executor=%s input=%s output=%s cache=%s",
                                           actual_executor_id,
                                           usage.get("input_token_count", 0),
                                           usage.get("output_token_count", 0),
                                           usage.get("cache_read_input_token_count", 0))
                                streaming_tokens_in += usage.get("input_token_count", 0) or 0
                                streaming_tokens_out += usage.get("output_token_count", 0) or 0
                                streaming_cache_hit += usage.get("cache_read_input_token_count", 0) or 0

                # Store step name for token events (for any late-arriving tokens)
                step_tracker[actual_executor_id] = step_num

                yield {
                    "event": "workflow_step",
                    "data": json.dumps({
                        "step_id": actual_executor_id,
                        "step_index": step_num,
                        "total_steps": total_steps,
                        "status": "completed",
                    }),
                }

            elif event_type == "executor_failed":
                step_num = step_tracker.get(executor_id, 0)
                details = event.data if event.data else None
                error_msg = str(details) if details else "Unknown step error"

                yield {
                    "event": "workflow_step",
                    "data": json.dumps({
                        "step_id": executor_id,
                        "step_index": step_num,
                        "total_steps": total_steps,
                        "status": "failed",
                        "error": {
                            "message": error_msg,
                            "type": "step_error",
                        },
                    }),
                }

            elif event_type == "executor_bypassed":
                step_num = step_tracker.get(executor_id, 0)
                yield {
                    "event": "workflow_step",
                    "data": json.dumps({
                        "step_id": executor_id,
                        "step_index": step_num,
                        "total_steps": total_steps,
                        "status": "bypassed",
                    }),
                }

            # ---- Output / intermediate text events ----
            # Handles both 'output' and 'intermediate' events.
            # These carry AgentResponse payloads from each workflow step.
            elif event_type in ("output", "intermediate"):
                data = event.data
                if data is not None:
                    # Debug: dump all attributes to understand the data structure
                    attrs = [a for a in dir(data) if not a.startswith("_")]
                    logger.info("PH-WORKFLOW-DATA: type=%s data_type=%s data_attrs=%s",
                               event_type, type(data).__name__, attrs)

                    # Accumulate token counts from usage_details
                    usage = getattr(data, "usage_details", None)
                    if usage is None and isinstance(data, dict):
                        usage = data.get("usage_details")
                    if usage is not None and isinstance(usage, dict):
                        logger.info("PH-WORKFLOW-USAGE: input=%s output=%s cache=%s",
                                   usage.get("input_token_count", 0),
                                   usage.get("output_token_count", 0),
                                   usage.get("cache_read_input_token_count", 0))
                        streaming_tokens_in += usage.get("input_token_count", 0) or 0
                        streaming_tokens_out += usage.get("output_token_count", 0) or 0
                        streaming_cache_hit += usage.get("cache_read_input_token_count", 0) or 0

                    # Try to extract text from the response
                    text = None
                    if hasattr(data, "text"):
                        text = str(data.text)
                    elif hasattr(data, "value"):
                        text = str(data.value)
                    elif isinstance(data, str):
                        text = data
                    elif isinstance(data, dict) and "content" in data:
                        text = str(data["content"])

                    if text:
                        # Track first token for TTFT calculation
                        if first_token_at_s is None:
                            first_token_at_s = time.monotonic()
                        # Look up step name from current streaming step
                        step_name = step_name_by_idx.get(current_step_idx, executor_id)
                        yield {
                            "event": "token",
                            "data": json.dumps({
                                "session_id": session_id,
                                "message_id": message_id,
                                "delta": text,
                                "step_name": step_name or "",
                            }),
                        }

            # ---- Error events ----
            elif event_type == "error":
                err_data = event.data
                error_msg = str(err_data) if err_data else "Workflow error"
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "message": error_msg,
                    }),
                }

            # ---- Warning events (optional passthrough) ----
            elif event_type == "warning":
                warn_msg = event.data or "Workflow warning"
                yield {
                    "event": "warning",
                    "data": json.dumps({
                        "message": warn_msg,
                    }),
                }

            # ---- Request info events (token usage) ----
            elif event_type == "request_info":
                data = event.data
                if data is not None:
                    usage = getattr(data, "usage_details", None)
                    if usage is None and isinstance(data, dict):
                        usage = data.get("usage_details")
                    if usage is not None and isinstance(usage, dict):
                        logger.info("PH-WORKFLOW-REQUESTUSAGE: input=%s output=%s cache=%s",
                                   usage.get("input_token_count", 0),
                                   usage.get("output_token_count", 0),
                                   usage.get("cache_read_input_token_count", 0))
                        streaming_tokens_in += usage.get("input_token_count", 0) or 0
                        streaming_tokens_out += usage.get("output_token_count", 0) or 0
                        streaming_cache_hit += usage.get("cache_read_input_token_count", 0) or 0

    except asyncio.CancelledError:
        # Stream was cancelled — propagate
        raise
    except Exception as exc:
        logger.warning("Workflow streaming error: %s", exc)
        yield {
            "event": "error",
            "data": json.dumps({
                "message": f"Workflow streaming error: {exc}",
            }),
        }

    logger.info("PH-WORKFLOW-ALLTYPES: %s", sorted(event_types_seen))

    # Final token extraction and message completion
    if token_counts is not None:
        try:
            final_result = await response_stream.get_final_response()
            logger.info("PH-WORKFLOW-FINAL-result type=%s final_result_attrs=%s",
                       type(final_result).__name__, [a for a in dir(final_result) if not a.startswith('_')])
            tokens_in, tokens_out, cache_hit = _extract_token_counts_from_workflow(final_result)
            # Use streaming accumulators if they have values (they may be more accurate),
            # otherwise fall back to the values extracted from the final result.
            final_tokens_in = streaming_tokens_in or tokens_in
            final_tokens_out = streaming_tokens_out or tokens_out
            final_cache_hit = streaming_cache_hit or cache_hit
            logger.info("PH-WORKFLOW-FINAL: streaming=(%d,%d,%d) result=(%d,%d,%d) => final=(%d,%d,%d)",
                       streaming_tokens_in, streaming_tokens_out, streaming_cache_hit,
                       tokens_in, tokens_out, cache_hit,
                       final_tokens_in, final_tokens_out, final_cache_hit)
            token_counts["in"] = final_tokens_in
            token_counts["out"] = final_tokens_out
            token_counts["cache_hit"] = final_cache_hit
            # Also store system_prompt/tools for metrics estimation
            token_counts["_system_prompt"] = system_prompt or ""
            token_counts["_tools"] = tools or []

            # Compute and emit metrics for session usage aggregation
            _emit_workflow_metrics(
                token_counts, total_steps, turn_start_s, first_token_at_s,
                final_tokens_in, final_tokens_out, final_cache_hit,
            )

            yield {
                "event": "message_complete",
                "data": json.dumps({
                    "session_id": session_id,
                    "message_id": message_id,
                    "total_tokens": final_tokens_in + final_tokens_out,
                    "tokens_in": final_tokens_in,
                    "tokens_out": final_tokens_out,
                    "cache_hit": final_cache_hit,
                }),
            }
        except Exception as exc:
            logger.warning("Could not extract token counts from workflow result: %s", exc)
            # Still yield message_complete with zeros
            token_counts["in"] = 0
            token_counts["out"] = 0
            token_counts["cache_hit"] = 0
            yield {
                "event": "message_complete",
                "data": json.dumps({
                    "session_id": session_id,
                    "message_id": message_id,
                    "total_tokens": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cache_hit": 0,
                }),
            }
            # Still emit basic metrics even on extraction failure
            _emit_workflow_metrics(token_counts, total_steps, turn_start_s, first_token_at_s, 0, 0, 0)


# ===========================================================================
# Token estimation helpers
# ===========================================================================

def _estimate_tokens(text: str) -> int:
    """Rough token count estimate: ~4 chars per token."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _estimate_tool_definitions(tools: list | None) -> int:
    """Rough token count for tool definition strings."""
    if not tools:
        return 0
    total = 0
    for t in tools:
        if isinstance(t, dict):
            total += _estimate_tokens(json.dumps(t))
        elif isinstance(t, str):
            total += _estimate_tokens(t)
    return max(1, total)


def _emit_workflow_metrics(
    token_counts: dict,
    total_steps: int,
    turn_start_s: float,
    first_token_at_s: float | None,
    tokens_in: int,
    tokens_out: int,
    cache_hit_tokens: int,
) -> None:
    """Compute and set the metrics dict for workflow execution."""
    if token_counts is None:
        return
    turn_end_s = time.monotonic()
    turn_wall_ms = int(round((turn_end_s - turn_start_s) * 1000))

    # For workflows, we don't see internal tool calls per-se, so tool_ms ≈ 0
    # and llm_ms ≈ wall time.
    llm_ms = turn_wall_ms
    tool_ms = 0
    ttft_ms = None if first_token_at_s is None else int(round((first_token_at_s - turn_start_s) * 1000))

    system_prompt_tokens = _estimate_tokens(token_counts.get("_system_prompt", ""))
    tool_definition_tokens = _estimate_tool_definitions(token_counts.get("_tools", []))

    if tokens_in > 0:
        messages_tokens = max(0, tokens_in - system_prompt_tokens - tool_definition_tokens)
    else:
        messages_tokens = None

    token_counts["metrics"] = {
        "llm_ms": llm_ms,
        "tool_ms": tool_ms,
        "ttft_ms": ttft_ms,
        "steps": total_steps,
        "cache_hit_tokens": cache_hit_tokens,
        "system_prompt_tokens": system_prompt_tokens if system_prompt_tokens > 0 else None,
        "tool_definition_tokens": tool_definition_tokens if tool_definition_tokens > 0 else None,
        "messages_tokens": messages_tokens,
    }
