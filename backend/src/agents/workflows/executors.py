"""Step input resolution wrapper for workflow steps.

This module provides :class:`StepAgent`, a ``SupportsAgentRun`` primitive that
applies a step's ``input`` semantics (empty/inherit, ``user_message``,
``output_of:<step_id>``, literal text) without changing the graph topology of
the workflow.  It wraps an inner agent so the executor count stays identical
to what ``build_workflow`` produces, preserving the per-step SSE indexing
used by :func:`engine.iter_workflow_sse`.

Output storage:
    Each step's final text is stored in ``shared["outputs"][step_id]`` in
    execution order.  Consequently ``output_of:<step_id>`` can only resolve
    steps that have already run — the definition validator already rejects
    unknown or self-referential ids at load time.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from agent_framework import AgentResponse, AgentResponseUpdate, Message


class StepAgent:
    """Wrap an inner agent to inject a step-specific input message.

    Attributes:
        id:            Executor identity (the step id).
        name:          Display name (delegated to inner or falls back to *step_id*).
        description:   Optional description (delegated to inner or ``None``).
    """

    def __init__(
        self,
        inner: Any,
        step_id: str,
        input_spec: str,
        shared: dict[str, Any],
    ) -> None:
        self._inner: Any = inner
        self._step_id: str = step_id
        self._input_spec: str = input_spec
        self._shared: dict[str, Any] = shared
        self.id: str = step_id
        self.name: str = getattr(inner, "name", step_id)
        self.description: str | None = getattr(inner, "description", None)

    def create_session(self):
        """Delegate to the inner agent."""
        return self._inner.create_session()

    # ------------------------------------------------------------------
    # Input resolution
    # ------------------------------------------------------------------

    def _effective_input(self) -> str | None:
        """Return the effective input text for this step.

        Four forms:

        * ``""`` → ``None`` (inherit upstream output).
        * ``"user_message"`` → the original workflow user message.
        * ``"output_of:<step_id>"`` → the text output of the named step.
        * anything else → the literal string itself.
        """
        if self._input_spec == "":
            return None
        if self._input_spec == "user_message":
            return self._shared.get("user_message")
        if self._input_spec.startswith("output_of:"):
            ref_id = self._input_spec.split(":", 1)[1]
            return self._shared.get("outputs", {}).get(ref_id)
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
          ``self._shared["outputs"][self._step_id]``.

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

        # Capture original user message before any rewrite
        self._shared.setdefault(
            "user_message",
            "".join(getattr(m, "text", "") or "" for m in msgs),
        )

        # Append effective input as an additional message
        effective = self._effective_input()
        if effective is not None and effective != "":
            msgs.append(Message("user", [effective]))

        if not stream:
            return self._run_non_streaming(msgs, session, **kwargs)
        else:
            return self._run_streaming(msgs, session, **kwargs)

    async def _run_non_streaming(
        self,
        messages: list,
        session: Any,
        **kwargs: Any,
    ) -> AgentResponse:
        """Non-streaming path: await inner.run, store result, return it."""
        response = await self._inner.run(
            messages, stream=False, session=session, **kwargs
        )
        self._shared.setdefault("outputs", {})[self._step_id] = response.text
        return response

    async def _run_streaming(
        self,
        messages: list,
        session: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[AgentResponseUpdate, None]:
        """Streaming path: yield each update, accumulate text, store at end."""
        inner_stream = self._inner.run(
            messages, stream=True, session=session, **kwargs
        )
        accumulated: list[str] = []

        async for update in inner_stream:
            yield update
            text_part = getattr(update, "text", "") or ""
            accumulated.append(text_part)

        self._shared.setdefault("outputs", {})[self._step_id] = "".join(
            accumulated
        )
