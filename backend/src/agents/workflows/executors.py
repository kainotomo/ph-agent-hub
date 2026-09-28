"""Step input resolution wrapper for workflow steps.

This module provides :class:`StepAgent`, a ``SupportsAgentRun`` primitive that
applies a step's ``input`` semantics (empty/inherit, ``user_message``,
``output_of:<step_id>``, literal text) without changing the graph topology of
the workflow.  It wraps an inner agent so the executor count stays identical
to what ``build_workflow`` produces, preserving the per-step SSE indexing
used by :func:`engine.iter_workflow_sse`.

Output storage:
    Each step's final text is stored in the MAF
    ``AgentSession.state["outputs"][step_id]`` so that per-step outputs and
    the ``user_message`` survive workflow checkpoint serialisation and
    restoration via ``AgentSession.to_dict()`` / ``from_dict()``.  The
    constructor's *shared* argument is the cross-step communication channel
    used by every step in a workflow.  After each step completes,
    ``_shared`` is synced into the MAF session state so that the checkpoint
    captures the full data.  On checkpoint restore the session state
    (including synced data) is restored automatically by MAF.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

import asyncio
import time

from agent_framework import AgentResponse, AgentResponseUpdate, Message

from src.core.exceptions import ValidationError
from src.models.cost import compute_run_cost


class StepAgent:
    """Wrap an inner agent to inject a step-specific input message.

    Attributes:
        id:            Executor identity (the step id).
        name:          Display name (delegated to inner or falls back to *step_id*).
        description:   Optional description (delegated to inner or ``None``).

    The constructor's *shared* argument is a mutable dict shared by every
    StepAgent in a workflow.  It carries ``user_message`` (the original
    workflow input) and ``outputs`` (``{step_id: text}``) across steps.
    At the end of each step's ``run()`` the dict is synced into the MAF
    ``AgentSession.state`` so that per-step checkpoints capture the full
    data.
    """

    def __init__(
        self,
        inner: Any,
        step_id: str,
        input_spec: str,
        shared: dict[str, Any] | None = None,
        *,
        model: Any = None,
        max_total_tokens: int | None = None,
        max_cost: float | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._inner: Any = inner
        self._step_id: str = step_id
        self._input_spec: str = input_spec
        self._shared: dict[str, Any] = shared if shared is not None else {}
        self.id: str = step_id
        self.name: str = getattr(inner, "name", step_id)
        self.description: str | None = getattr(inner, "description", None)
        self._model = model
        self._max_total_tokens = max_total_tokens
        self._max_cost = max_cost
        self._timeout_seconds = timeout_seconds

    def create_session(self):
        """Delegate to the inner agent."""
        return self._inner.create_session()

    # ------------------------------------------------------------------
    # State accessor
    # ------------------------------------------------------------------

    def _state(self, session: Any) -> dict[str, Any]:
        """Return the cross-step shared mutable dict.

        *Writes* always go to ``self._shared`` so that every step in a
        workflow communicates via the same dict.  At the end of each
        ``run()`` the shared dict is synced into the MAF session state for
        checkpoint persistence.
        """
        return self._shared

    # ------------------------------------------------------------------
    # Budget enforcement
    # ------------------------------------------------------------------

    def _assert_budget(self, state: dict[str, Any]) -> None:
        """Raise ``ValidationError`` when accumulated spend already meets a ceiling.

        Tolerates a missing ``_budget`` key (treats as zero spend) and a
        ``None`` ceiling (no limit).  Does not raise when both ceilings are
        ``None``.
        """
        budget = state.get("_budget") or {}
        tokens_in = budget.get("tokens_in", 0) or 0
        tokens_out = budget.get("tokens_out", 0) or 0
        cost = budget.get("cost", 0.0) or 0.0

        total_tokens = tokens_in + tokens_out

        if self._max_total_tokens is not None and total_tokens >= self._max_total_tokens:
            raise ValidationError(
                f"Step '{self._step_id}: per-run budget exceeded "
                f"(total tokens {total_tokens} >= {self._max_total_tokens})'"
            )
        if self._max_cost is not None and cost >= self._max_cost:
            raise ValidationError(
                f"Step '{self._step_id}: per-run budget exceeded "
                f"(cost {cost} >= {self._max_cost})'"
            )

    def _record_spend(
        self, state: dict[str, Any], response: AgentResponse
    ) -> None:
        """Record token/cost spend from a response into the budget dict.

        Reads ``response.usage_details`` (a dict with
        ``input_token_count``, ``output_token_count``,
        ``cache_read_input_token_count``) when present.  If no usage is
        available, records nothing and does not fail.
        """
        usage = getattr(response, "usage_details", None)
        if usage is None or not isinstance(usage, dict):
            return

        tokens_in = usage.get("input_token_count") or 0
        tokens_out = usage.get("output_token_count") or 0
        cache_hit = usage.get("cache_read_input_token_count") or 0

        budget = state.setdefault(
            "_budget", {"tokens_in": 0, "tokens_out": 0, "cost": 0.0}
        )
        budget["tokens_in"] = (budget.get("tokens_in") or 0) + tokens_in
        budget["tokens_out"] = (budget.get("tokens_out") or 0) + tokens_out
        budget["cost"] = (budget.get("cost") or 0.0) + compute_run_cost(
            self._model, tokens_in, tokens_out, cache_hit
        )

    # ------------------------------------------------------------------
    # Two-source lookup
    # ------------------------------------------------------------------

    def _effective_input(self, state: dict[str, Any], session: Any) -> str | None:
        """Return the effective input text for this step.

        Two-source lookup: first checks ``session.state`` (for pre-seeded
        values or values synced from a previous step on the same session),
        then falls back to *state* (``self._shared`` — the cross-step
        communication dict shared across all steps in a workflow).

        Four input spec forms:

        * ``""`` → ``None`` (inherit upstream output).
        * ``"user_message"`` → the original workflow user message.
        * ``"output_of:<step_id>"`` → the text output of the named step.
        * anything else → the literal string itself.
        """
        # Try session state first (pre-seeded or synced from previous run).
        sess_outputs = None
        sess_um = None
        if session is not None:
            s = getattr(session, "state", None)
            if isinstance(s, dict):
                sess_outputs = s.get("outputs")
                sess_um = s.get("user_message")

        if self._input_spec == "":
            # Inherit: prefer session, fall back to shared.
            if sess_outputs is not None:
                ref_id = state.get("_last_step_id", self._step_id)
                val = sess_outputs.get(ref_id)
                if val is not None:
                    return val
            if isinstance(state.get("outputs"), dict):
                return state["outputs"].get(self._step_id)
            return None
        if self._input_spec == "user_message":
            if sess_um is not None:
                return sess_um
            return state.get("user_message")
        if self._input_spec.startswith("output_of:"):
            ref_id = self._input_spec.split(":", 1)[1]
            # Prefer session state for pre-seeded or synced data.
            if isinstance(sess_outputs, dict):
                val = sess_outputs.get(ref_id)
                if val is not None:
                    return val
            # Fall back to shared dict.
            if isinstance(state.get("outputs"), dict):
                return state["outputs"].get(ref_id)
            return None
        return self._input_spec

    # ------------------------------------------------------------------
    # Agent run surface
    # ------------------------------------------------------------------

    def run(
        self,
        messages=None,
        *,
        stream: bool = False,
        session: Any = None,
        **kwargs: Any,
    ):
        """Run the wrapped agent with input-resolution applied.

        * Plain ``def``, not ``async def`` — MAF's ``AgentExecutor`` calls
          ``agent.run(..., stream=True)`` and immediately does ``async for
          update in stream``, so an async def would raise
          ``TypeError: 'async for' requires an object with __aiter__ method,
          got coroutine``.
        * When ``stream=False`` returns a coroutine.
        * When ``stream=True`` returns an async generator.
        * The original user message is captured before any rewrite so that
          ``"user_message"`` always refers to the workflow's top-level input
          regardless of which step asks for it.
        * Outputs are recorded per step in execution order in
          ``AgentSession.state["outputs"][self._step_id]`` when a session is
          present (synced from the shared dict after each step completes).

        Args:
            messages:  List of messages (may be ``None``).
            stream:    Whether to run in streaming mode.
            session:   Optional session to reuse.
            kwargs:    Extra keyword arguments forwarded verbatim.

        Returns:
            When ``stream`` is falsy: a coroutine yielding
            ``AgentResponse``.
            When ``stream`` is truthy: an async generator yielding
            ``AgentResponseUpdate`` objects.
        """
        # Never mutate the caller's list
        msgs = list(messages or [])

        # ------------------------------------------------------------------
        # Use the shared dict as the primary write source — same object across
        # all steps in a workflow.  Read also checks session.state first.
        # ------------------------------------------------------------------
        state = self._state(session)

        # ------------------------------------------------------------------
        # Capture the original user message only once (on first step that sees
        # a user message in the raw input).  Uses shared dict as storage so
        # all steps can read it.
        # ------------------------------------------------------------------
        if "user_message" not in state:
            for m in msgs:
                if getattr(m, "role", None) == "user":
                    text = getattr(m, "text", "") or ""
                    if text:
                        state["user_message"] = text
                        break

        # ------------------------------------------------------------------
        # Resolve effective input (reads from session state OR shared dict).
        # ------------------------------------------------------------------
        effective = self._effective_input(state, session)
        if effective is not None and effective != "":
            msgs.append(Message("user", [effective]))

        if not stream:
            return self._run_non_streaming(msgs, session, state, **kwargs)
        else:
            return self._run_streaming(msgs, session, state, **kwargs)

    async def _run_non_streaming(
        self,
        messages: list,
        session: Any,
        state: dict[str, Any],
        **kwargs: Any,
    ) -> AgentResponse:
        """Non-streaming path: await inner.run, store result, return it."""
        self._assert_budget(state)

        async def _run():
            return await self._inner.run(
                messages, stream=False, session=session, **kwargs
            )

        if self._timeout_seconds is not None:
            try:
                response = await asyncio.wait_for(_run(), self._timeout_seconds)
            except asyncio.TimeoutError:
                raise ValidationError(
                    f"Step '{self._step_id}: step timeout after "
                    f"{self._timeout_seconds}s'"
                )
        else:
            response = await _run()

        state.setdefault("outputs", {})[self._step_id] = response.text
        self._record_spend(state, response)
        self._sync_state_to_session(session, state)
        return response

    async def _run_streaming(
        self,
        messages: list,
        session: Any,
        state: dict[str, Any],
        **kwargs: Any,
    ) -> AsyncGenerator[AgentResponseUpdate, None]:
        """Streaming path: yield each update, accumulate text, store at end."""
        self._assert_budget(state)

        inner_stream = self._inner.run(
            messages, stream=True, session=session, **kwargs
        )
        accumulated: list[str] = []
        last_update: AgentResponseUpdate | None = None

        if self._timeout_seconds is None:
            async for update in inner_stream:
                last_update = update
                yield update
                accumulated.append(getattr(update, "text", "") or "")
        else:
            # Wall-clock ceiling for the whole step.  ``asyncio.wait_for``
            # cannot wrap an ``async for`` directly, so iterate the stream
            # explicitly and bound each ``__anext__`` by the time remaining
            # until the deadline.  A timeout raises — the run must stop, not
            # silently truncate.
            deadline = time.monotonic() + self._timeout_seconds
            iterator = inner_stream.__aiter__()
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValidationError(
                            f"Step '{self._step_id}: step timeout after "
                            f"{self._timeout_seconds}s'"
                        )
                    try:
                        update = await asyncio.wait_for(
                            iterator.__anext__(), remaining
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        raise ValidationError(
                            f"Step '{self._step_id}: step timeout after "
                            f"{self._timeout_seconds}s'"
                        )
                    last_update = update
                    yield update
                    accumulated.append(getattr(update, "text", "") or "")
            finally:
                aclose = getattr(inner_stream, "aclose", None)
                if aclose is not None:
                    await aclose()

        state.setdefault("outputs", {})[self._step_id] = "".join(
            accumulated
        )
        if last_update is not None:
            self._record_spend(state, last_update)
        self._sync_state_to_session(session, state)

    def _sync_state_to_session(self, session: Any, state: dict[str, Any]) -> None:
        """Sync the cross-step state dict into the MAF session state.

        This ensures that every workflow checkpoint (which serialises
        ``AgentSession.to_dict()``) captures the full step data including
        ``user_message`` and ``outputs``.
        """
        if session is not None and isinstance(
            getattr(session, "state", None), dict
        ):
            for k, v in state.items():
                session.state[k] = v
