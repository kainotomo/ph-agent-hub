# =============================================================================
# PH Agent Hub — Workflow Engine
# =============================================================================
# Builds and executes MAF 1.19.0 workflows from ``WorkflowDefinition`` objects.
#
# Public API:
#   ``build_workflow(defn, db, tenant_id, extra_tools)`` → built ``Workflow`` instance
#   ``run_workflow(workflow, message, **kwargs)``    → ``tuple[str, WorkflowRunResult]``
#   ``iter_workflow_sse(workflow, message, ...)``    → ``AsyncIterator[dict]`` SSE events
#   ``resolve_model(db, key, tenant_id)``           → ``Model`` from DB (tenant-scoped)
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
    WorkflowRunState,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions import NotFoundError, ValidationError
from ...db.orm.models import Model
from .definition import WorkflowDefinition
from .executors import StepAgent
from .roles import TOOL_ROLE_TARGETS, is_role_reference

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


async def resolve_model(db: AsyncSession, model_ref: str, tenant_id: str) -> Model:
    """Resolve a model reference for a tenant.

    Two reference kinds are supported:

    * ``@role`` — a logical role resolved through the tenant's role
      bindings (``services.model_role_service``).  An unbound role raises
      ``ValidationError`` naming the role; it must never fall back to an
      arbitrary model.
    * anything else — a concrete tenant model, matched against ``Model.id``
      or ``Model.model_id`` within *tenant_id* only.

    Raises:
        ValidationError: If the role is unbound, or the concrete reference
            matches a model owned by a different tenant.
        NotFoundError: If the concrete reference does not resolve for this
            tenant.
    """
    from sqlalchemy import select

    if model_ref is not None and is_role_reference(model_ref):
        from ...services.model_role_service import resolve_role_model

        model = await resolve_role_model(db, tenant_id, model_ref)
        if model is None:
            raise ValidationError(
                f"No model bound to role '{model_ref}' for tenant '{tenant_id}'"
            )
        return model

    result = await db.execute(
        select(Model)
        .where(
            (Model.id == model_ref) | (Model.model_id == model_ref),
            Model.tenant_id == tenant_id,
        )
        .order_by(Model.created_at.asc(), Model.id.asc())
        .limit(1)
    )
    model = result.scalars().first()
    if model is not None:
        return model

    # Distinguish "belongs to another tenant" from "does not exist" so that a
    # cross-tenant reference is rejected loudly instead of being rescued by
    # the caller's default-model fallback.
    diagnostic = await db.execute(
        select(Model)
        .where((Model.id == model_ref) | (Model.model_id == model_ref))
        .limit(1)
    )
    foreign = diagnostic.scalars().first()
    if foreign is not None and foreign.tenant_id != tenant_id:
        raise ValidationError(
            f"Model '{model_ref}' belongs to a different tenant"
        )

    raise NotFoundError(
        f"Model not found for reference '{model_ref}'"
    )


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
        ValidationError: If the module has no workflow definition structure, or
            if a declared ``WORKFLOW_DEFINITION`` key does not match the
            module's ``MAF_KEY``.
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
        # The key is the checkpoint namespace and the registry key, so a
        # rename is a create, not an edit: it must track the module MAF_KEY.
        # (The STEPS branch below cannot diverge: it derives key from MAF_KEY.)
        maf_key = getattr(mod, "MAF_KEY", None)
        if isinstance(maf_key, str) and maf_key != defn.key:
            raise ValidationError(
                f"Workflow definition key '{defn.key}' does not match module "
                f"MAF_KEY '{maf_key}': a workflow key is immutable, renaming it is "
                f"a create, not an edit"
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


def _resolve_step_tools(
    step: Any, tool_pool: list | None, workflow_key: str
) -> list:
    """Select the tools a single workflow step may use.

    Refs are matched against the already-resolved, tenant-scoped tool pool by
    MAF tool-callable name.  This is a *restriction* filter — it can never
    supply a tool the run did not resolve, so a tenant tool that is disabled
    or not active for the run simply is not available and the build fails.

    An ``@``-prefixed ref is a role from the closed vocabulary, resolved
    through ``TOOL_ROLE_TARGETS``; any other ref is matched directly.

    An empty ``tool_refs`` inherits the whole pool, so definitions that do not
    restrict tools keep today's behaviour.  A ref matching nothing in the pool
    raises ``ValidationError`` naming the step and the ref — a step silently
    running without its tools is the failure mode this exists to remove.

    Args:
        step:         ``WorkflowStep`` whose ``tool_refs`` drive selection.
        tool_pool:    Already-resolved tool callables for this run.
        workflow_key: Workflow key, used in error messages.

    Returns:
        The selected callables in pool order, de-duplicated.
    """
    pool = list(tool_pool or [])
    refs = list(getattr(step, "tool_refs", None) or [])
    if not refs:
        return pool

    by_name: dict[str, Any] = {}
    for tool in pool:
        name = getattr(tool, "name", None)
        if name:
            by_name.setdefault(name, tool)

    wanted: set[str] = set()
    unresolved: list[str] = []

    for ref in refs:
        if is_role_reference(ref):
            names = TOOL_ROLE_TARGETS.get(ref)
            if names is None:
                raise ValidationError(
                    f"Step '{step.id}': tool role '{ref}' has no declared target"
                )
        else:
            names = (ref,)

        wanted.update(names)
        if not any(name in by_name for name in names) and ref not in unresolved:
            unresolved.append(ref)

    if unresolved:
        available = ", ".join(sorted(by_name)) or "(none)"
        raise ValidationError(
            f"Workflow '{workflow_key}': step '{step.id}' tool refs "
            f"{sorted(unresolved)} are not available in this run. "
            f"Available tools: {available}"
        )

    return [tool for tool in pool if getattr(tool, "name", None) in wanted]


# ---------------------------------------------------------------------------
# Workflow builder
# ---------------------------------------------------------------------------


async def build_workflow(
    defn: WorkflowDefinition,
    db: AsyncSession,
    tenant_id: str,
    extra_tools: list | None = None,
    base_temperature: float = 0.7,
    base_reasoning_effort: str | None = None,
    default_model_id: str | None = None,
    *,
    checkpoint_storage: Any | None = None,
    workflow_name: str | None = None,
    initial_state: dict[str, Any] | None = None,
) -> Workflow:
    """Build a MAF ``Workflow`` from a ``WorkflowDefinition``.

    Steps are connected sequentially via ``WorkflowBuilder.add_chain``.
    The final step's output becomes the workflow output.

    Args:
        defn:                  Workflow definition with steps.
        db:                    Database session (used to resolve Model records).
        tenant_id:             Tenant owning the run; all model resolution is scoped to it.
        extra_tools:           Optional global tools injected into every step.
        base_temperature:      Base temperature for all steps (overridden by step).
        base_reasoning_effort: Base reasoning effort for all steps (overridden by step).
        default_model_id:      Fallback model id to use when a step's model_ref is not
                               found in the DB (typically the skill's default model).
        checkpoint_storage:    Optional MAF ``CheckpointStorage`` for persisting and
                               restoring workflow checkpoints across runs.
        workflow_name:         Explicit name for the MAF workflow. When omitted the
                               name defaults to ``"{tenant_id}:{defn.key}"`` so that
                               checkpoints are namespaced per tenant.
        initial_state:         Checkpoint-recovered cross-step state dict. Passed to the
                               builder so that steps which already ran keep contributing
                               to ``output_of:`` and ``user_message`` on a resumed run.

    Returns:
        A built ``Workflow`` instance ready for execution.

    Raises:
        ValidationError: If step configuration is invalid.
    """
    # Build steps into agents + executors
    executors: list[AgentExecutor] = []
    # Seed the run-wide cross-step state.  On a resumed run the caller passes
    # the state snapshot recovered from the checkpoint so that steps which
    # already ran keep contributing to `output_of:` and `user_message`.
    shared: dict[str, Any] = dict(initial_state or {})

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

        # Resolve model for this step. Only an unresolved *concrete* reference
        # may fall back to the skill's default model: an unbound role or a
        # cross-tenant reference raises ValidationError and must surface.
        model = None
        try:
            model = await resolve_model(db, model_ref, tenant_id)
        except NotFoundError:
            if default_model_id:
                logger.warning(
                    "Step '%s' model_ref '%s' not found, falling back to '%s'",
                    step.id, model_ref, default_model_id,
                )
                model = await resolve_model(db, default_model_id, tenant_id)
            else:
                raise

        # Resolve tools for this step: the run's tenant-scoped pool,
        # restricted by the step's tool_refs (empty = inherit the pool).
        step_tools = _resolve_step_tools(step, extra_tools, defn.key)

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
    # MAF groups checkpoints by workflow name and validates them by graph
    # signature hash.  Namespacing the name per tenant means two tenants that
    # ship the same definition key cannot see each other's checkpoints.  The
    # hash is derived from topology only, so the name does not affect it.
    effective_name = workflow_name or f"{tenant_id}:{defn.key}"
    builder = WorkflowBuilder(
        name=effective_name,
        description=defn.description,
        start_executor=executors[0],
        output_executors=[executors[-1]],
        checkpoint_storage=checkpoint_storage,
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


def workflow_outcome(result: Any) -> str:
    """Return ``"paused"`` when a run is idle awaiting input, else ``"completed"``.

    MAF distinguishes ``WorkflowRunState.IDLE`` (finished) from
    ``WorkflowRunState.IDLE_WITH_PENDING_REQUESTS`` (paused, awaiting a
    response).  Inferring completion from the end of the event stream would
    report a paused run as finished, so the run state is read explicitly.
    """
    try:
        state = result.get_final_state()
    except Exception:
        return "completed"
    if state is WorkflowRunState.IDLE_WITH_PENDING_REQUESTS:
        return "paused"
    return "completed"


# ---------------------------------------------------------------------------
# Workflow execution (non-streaming)
# ---------------------------------------------------------------------------


async def run_workflow(
    workflow: Workflow,
    message: str | None = None,
    function_invocation_kwargs: dict | None = None,
    *,
    checkpoint_storage: Any | None = None,
    checkpoint_id: str | None = None,
    responses: dict | None = None,
) -> tuple[str, Any]:  # tuple[str, WorkflowRunResult]
    """Execute a workflow synchronously and return (output_text, result).

    Args:
        workflow:                  Built MAF workflow.
        message:                   User message to pass to the workflow.
        function_invocation_kwargs: Optional kwargs forwarded to agent.run().
        checkpoint_storage:        Optional MAF checkpoint storage for persisting/restoring runs.
        checkpoint_id:             ID of a checkpoint to resume from.
        responses:                 Optional MAF responses dict, forwarded as-is for
                                   workflow resume.

    Returns:
        Tuple of (final_output_text, WorkflowRunResult).

    Raises:
        ValidationError: If workflow execution fails.

    Note:
        MAF requires ``message`` and ``checkpoint_id`` to be mutually exclusive.
        Passing ``checkpoint_id`` with ``message=None`` is the resume path.
    """
    kwargs: dict[str, Any] = {}
    if function_invocation_kwargs:
        kwargs["function_invocation_kwargs"] = function_invocation_kwargs
    if checkpoint_storage is not None:
        kwargs["checkpoint_storage"] = checkpoint_storage
    if checkpoint_id is not None:
        kwargs["checkpoint_id"] = checkpoint_id
    if responses:
        kwargs["responses"] = responses

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
# Approval event helper
# ---------------------------------------------------------------------------


def approval_event_from_request_info(event: Any) -> dict | None:
    """Build the SSE payload for a function-approval request_info event.

    Returns None for any request_info that is not a function approval request.
    """
    data = event.data
    if data is None or getattr(data, "type", None) != "function_approval_request":
        return None
    function_call = getattr(data, "function_call", None)
    return {
        "type": "function_approval_request",
        "request_id": getattr(event, "request_id", ""),
        "step_id": _get_step_id_from_event(event),
        "tool_name": getattr(function_call, "name", None),
        "arguments": getattr(function_call, "arguments", None),
    }


def _get_step_id_from_event(event: Any) -> str:
    """Extract step_id from an event's source_executor_id, returning '' on failure."""
    try:
        return getattr(event, "source_executor_id", "") or ""
    except (ValueError, AttributeError, RuntimeError):
        return ""


# ---------------------------------------------------------------------------
# Streaming: iterate workflow events and yield SSE dicts
# ---------------------------------------------------------------------------


async def iter_workflow_sse(
    workflow: Workflow,
    *,
    message: str | None = None,
    session_id: str,
    message_id: str,
    token_counts: dict | None = None,
    function_invocation_kwargs: dict | None = None,
    system_prompt: str | None = None,
    tools: list | None = None,
    checkpoint_storage: Any | None = None,
    checkpoint_id: str | None = None,
    responses: dict | None = None,
) -> AsyncIterator[dict]:
    """Stream a workflow execution, yielding SSE event dicts per step.

    SSE event types emitted:
        - ``workflow_step``: Per-step lifecycle events
          {event: "workflow_step", data: {workflow_key, step_id, step_index, total_steps, status}}
        - ``token``: Token events from individual steps (delta text)
        - ``tool_start``, ``tool_result``: Tool execution events
        - ``status``: Run-state events; consumed internally and **not** yielded.
        - ``message_complete``: Final completion with token counts, metrics,
          ``outcome`` (``"completed"`` or ``"paused"``), and
          ``pending_request_ids`` (present only when ``outcome == "paused"``).
        - ``workflow_approval_required``: Emitted when the workflow pauses on a
          function-approval request_info event.  Data is a dict with
          ``type``, ``request_id``, ``step_id``, ``tool_name``, and ``arguments``.

    Args:
        workflow:                  Built MAF workflow.
        message:                   User message (``None`` for resumed runs).
        session_id:                Session ID for SSE tracking.
        message_id:                Message ID for SSE tracking.
        token_counts:              Mutable dict for accumulating token counts.
        function_invocation_kwargs: Optional kwargs forwarded to agent.run().
        system_prompt:             System prompt string for token estimation.
        tools:                     List of tool definitions for token estimation.
        checkpoint_storage:        Optional MAF checkpoint storage for persisting/restoring runs.
        checkpoint_id:             ID of a checkpoint to resume from.
        responses:                 Optional MAF ``responses`` dict, forwarded as-is
                                   for workflow resume (function-call approvals).

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
    if checkpoint_storage is not None:
        stream_kwargs["checkpoint_storage"] = checkpoint_storage
    if checkpoint_id is not None:
        stream_kwargs["checkpoint_id"] = checkpoint_id
    if responses:
        stream_kwargs["responses"] = responses

    # Run the workflow with streaming
    response_stream = workflow.run(message, **stream_kwargs)

    try:
        # Accumulators for token counts during streaming
        streaming_tokens_in = 0
        streaming_tokens_out = 0
        streaming_cache_hit = 0
        event_types_seen = set()
        processed_event_types = set()
        paused_with_pending = False

        # Iterate over the async stream of WorkflowEvent objects
        async for event in response_stream:
            event_type = event.type
            event_types_seen.add(event_type)
            # source_executor_id is only available on certain event types
            # (e.g. request_info); accessing it on others raises ValueError
            try:
                executor_id = getattr(event, "source_executor_id", None) or ""
            except (ValueError, AttributeError, RuntimeError):
                executor_id = ""

            if event_type == "request_info":
                logger.debug("Workflow request_info event: executor=%s request_id=%s",
                             executor_id, getattr(event, "request_id", None))

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

            # ---- Run-state transitions ----
            # MAF emits a status event for each run-state change.
            # IDLE_WITH_PENDING_REQUESTS means the workflow paused awaiting a
            # response and must not be reported as finished.  Status events are
            # consumed here and are NOT yielded to the SSE client.
            elif event_type == "status":
                if getattr(event, "state", None) is WorkflowRunState.IDLE_WITH_PENDING_REQUESTS:
                    paused_with_pending = True

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

                approval = approval_event_from_request_info(event)
                if approval is not None:
                    yield {"event": "workflow_approval_required", "data": json.dumps(approval)}

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
            outcome = workflow_outcome(final_result)
            if paused_with_pending:
                outcome = "paused"

            pending_request_ids = [
                getattr(ev, "request_id", None)
                for ev in final_result.get_request_info_events()
            ]
            pending_request_ids = [rid for rid in pending_request_ids if rid]
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
            token_counts["outcome"] = outcome
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
                    "outcome": outcome,
                    "pending_request_ids": pending_request_ids if outcome == "paused" else [],
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
                    "outcome": "paused" if paused_with_pending else "completed",
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
