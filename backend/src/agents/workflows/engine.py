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

logger = logging.getLogger(__name__)


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
            return WorkflowDefinition(**wf_def)
        elif isinstance(wf_def, WorkflowDefinition):
            return wf_def
        else:
            raise ValidationError(
                "WORKFLOW_DEFINITION must be a dict or WorkflowDefinition instance"
            )

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
        return WorkflowDefinition(
            key=key,
            name=name,
            description=description,
            steps=steps,
        )

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
) -> Agent:
    """Create a MAF ``Agent`` for a workflow step.

    Args:
        step:             WorkflowStep with instructions, name, etc.
        model:            Model record for LLM client creation.
        tools:            Optional list of tool callables.
        temperature:      Model temperature.
        reasoning_effort: Optional CoT effort override.

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
        instructions=step.instructions or "",
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

    for i, step in enumerate(defn.steps):
        # Resolve model for this step (with fallback to skill default)
        model = None
        try:
            model = await resolve_model(db, step.model_ref)
        except NotFoundError:
            if default_model_id:
                logger.warning(
                    "Step '%s' model_ref '%s' not found, falling back to '%s'",
                    step.id, step.model_ref, default_model_id,
                )
                model = await resolve_model(db, default_model_id)
            else:
                raise

        # Resolve tools for this step (merge extra tools with step-specific)
        step_tools = list(extra_tools) if extra_tools else []
        # TODO: resolve step-specific tools from tool_names in future

        # Build agent for this step
        temperature = step.temperature if step.temperature != 0.7 else base_temperature
        reasoning_effort = step.reasoning_effort or base_reasoning_effort

        agent = _build_agent_for_step(
            step=step,
            model=model,
            tools=step_tools,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

        # Wrap in executor with the step's id as executor id
        executor = AgentExecutor(agent=agent, id=step.id)
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
        if event.type not in ("output", "intermediate"):
            continue
        data = event.data
        if data is None:
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
) -> AsyncIterator[dict]:
    """Stream a workflow execution, yielding SSE event dicts per step.

    SSE event types emitted:
        - ``workflow_step``: Per-step lifecycle events
          {event: "workflow_step", data: {workflow_key, step_id, step_index, total_steps, status}}
        - ``token``: Token events from individual steps (delta text)
        - ``tool_start``, ``tool_result``: Tool execution events
        - ``message_complete``: Final completion with token counts

    Args:
        workflow:                  Built MAF workflow.
        message:                   User message.
        session_id:                Session ID for SSE tracking.
        message_id:                Message ID for SSE tracking.
        token_counts:              Mutable dict for accumulating token counts.
        function_invocation_kwargs: Optional kwargs forwarded to agent.run().

    Yields:
        SSE event dicts.
    """
    from agent_framework import WorkflowEventType

    # Get total steps for progress reporting
    total_steps = len(workflow.get_executors_list())

    step_index = 0
    step_tracker: dict[str, int] = {}  # executor_id → step_index

    # Build kwargs for the stream call
    stream_kwargs: dict[str, Any] = {"stream": True}
    if function_invocation_kwargs:
        stream_kwargs["function_invocation_kwargs"] = function_invocation_kwargs

    # Run the workflow with streaming
    response_stream = workflow.run(message, **stream_kwargs)

    try:
        # Iterate over the async stream of WorkflowEvent objects
        async for event in response_stream:
            event_type = event.type
            # source_executor_id is only available on certain event types
            # (e.g. request_info); accessing it on others raises ValueError
            try:
                executor_id = getattr(event, "source_executor_id", None) or ""
            except (ValueError, AttributeError, RuntimeError):
                executor_id = ""

            # ---- Step lifecycle events ----
            if event_type == "executor_invoked":
                # First time we see this executor, assign it an index
                if executor_id not in step_tracker:
                    step_tracker[executor_id] = step_index
                    step_index += 1
                step_num = step_tracker[executor_id]

                yield {
                    "event": "workflow_step",
                    "data": json.dumps({
                        "step_id": executor_id,
                        "step_index": step_num,
                        "total_steps": total_steps,
                        "status": "started",
                    }),
                }

            elif event_type == "executor_completed":
                step_num = step_tracker.get(executor_id, 0)
                yield {
                    "event": "workflow_step",
                    "data": json.dumps({
                        "step_id": executor_id,
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
            elif event_type == "output":
                data = event.data
                if data is not None:
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
                        yield {
                            "event": "token",
                            "data": json.dumps({
                                "session_id": session_id,
                                "message_id": message_id,
                                "delta": text,
                                "step_name": executor_id or "",
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

    # Final token extraction and message completion
    if token_counts is not None:
        try:
            final_result = await response_stream.get_final_response()
            tokens_in, tokens_out, cache_hit = _extract_token_counts_from_workflow(final_result)
            token_counts["in"] = tokens_in
            token_counts["out"] = tokens_out
            token_counts["cache_hit"] = cache_hit

            yield {
                "event": "message_complete",
                "data": json.dumps({
                    "session_id": session_id,
                    "message_id": message_id,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cache_hit": cache_hit,
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
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "cache_hit": 0,
                }),
            }
