# =============================================================================
# PH Agent Hub — DeepSeek Provider Client
# =============================================================================

import json
import logging
from typing import Any, Mapping, Sequence

from agent_framework._types import Content, Message
from agent_framework.openai import OpenAIChatCompletionClient
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from ..db.orm.models import Model

logger = logging.getLogger(__name__)


class DeepSeekThinkingClient(OpenAIChatCompletionClient):
    """OpenAI-compatible client with DeepSeek thinking mode support.

    When ``thinking_enabled=True``, adds ``extra_body={"thinking": {"type":
    "enabled"}}`` to every request.  Handles the ``reasoning_content``
    field in both non-streaming and streaming responses, converting it to
    ``Content`` items of type ``text_reasoning``.
    """

    def __init__(self, *args: Any, thinking_enabled: bool = False, reasoning_effort: str | None = None, **kwargs: Any) -> None:
        self._thinking_enabled = thinking_enabled
        self._reasoning_effort = reasoning_effort
        super().__init__(*args, **kwargs)

    # ---- Overrides --------------------------------------------------------

    def _prepare_options(
        self,
        messages: Sequence[Message],
        options: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Inject ``extra_body`` with explicit thinking mode.

        When thinking is enabled, keep it enabled across all turns —
        including after tool calls.  The _prepare_message_for_openai
        override correctly round-trips reasoning_content from either
        additional_properties or the concatenated Content.text, so
        the DeepSeek requirement is satisfied.
        """
        result = super()._prepare_options(messages, options)

        thinking_type = "enabled" if self._thinking_enabled else "disabled"

        # Inject reasoning_effort when thinking is enabled and a value is set
        if thinking_type == "enabled" and self._reasoning_effort:
            result["reasoning_effort"] = self._reasoning_effort
        elif "reasoning_effort" in result:
            # Base class may have propagated reasoning_effort from options;
            # DeepSeek requires it not be set when thinking is disabled.
            del result["reasoning_effort"]

        result["extra_body"] = {
            "thinking": {"type": thinking_type}
        }
        return result

    def _parse_response_from_openai(
        self,
        response: ChatCompletion,
        options: Mapping[str, Any],
    ) -> Any:  # ChatResponse
        """Extract ``reasoning_content`` and stabilise tool calls from DeepSeek response.

        DeepSeek models sometimes output tool calls as text markers
        (``🔧 function_name``) instead of proper OpenAI tool_calls.
        The stabiliser extracts these and injects them as proper
        function call objects that MAF can process.
        """
        # ---- Stabiliser: detect & inject tool calls from text content ----
        from ..agents.stabilizer import (
            strip_reasoning,
            extract_json,
            repair_json,
        )
        from ..agents.deepseek_patch import extract_json_block

        for choice in response.choices:
            msg = choice.message
            content = msg.content or ""
            # Only intervene when there are no native tool_calls AND the
            # content looks like it contains tool-call syntax.
            if (
                not msg.tool_calls
                and content
                and ("🔧" in content or "```json" in content or '"name"' in content)
            ):
                # Step 1: strip reasoning traces
                cleaned = strip_reasoning(content)

                # Step 2: try to extract JSON tool call blocks
                json_block = extract_json_block(cleaned)
                if json_block and json_block != cleaned:
                    try:
                        repaired = repair_json(json_block)
                        import json as _json
                        parsed = _json.loads(repaired)

                        # Wrap single tool call into list form
                        tool_calls_list = parsed if isinstance(parsed, list) else [parsed]
                        if isinstance(tool_calls_list, list) and tool_calls_list:
                            # Inject tool calls into the OpenAI response message
                            from openai.types.chat import (
                                ChatCompletionMessageToolCall,
                            )
                            from openai.types.chat.chat_completion_message_tool_call import Function

                            injected: list = []
                            for tc in tool_calls_list:
                                if not isinstance(tc, dict):
                                    continue
                                fn_name = tc.get("name") or tc.get("function") or tc.get("tool", "")
                                fn_args = tc.get("arguments") or tc.get("input") or {}
                                if not fn_name:
                                    continue
                                if isinstance(fn_args, dict):
                                    fn_args = _json.dumps(fn_args)
                                injected.append(
                                    ChatCompletionMessageToolCall(
                                        id=f"call_deepseek_{len(injected)}",
                                        function=Function(
                                            name=str(fn_name),
                                            arguments=str(fn_args),
                                        ),
                                        type="function",
                                    )
                                )

                            if injected:
                                # Build a new message with injected tool_calls
                                msg.tool_calls = injected
                                msg.content = cleaned  # preserve cleaned text
                                logger.info(
                                    "Stabiliser injected %d tool call(s) from DeepSeek text response",
                                    len(injected),
                                )
                    except Exception:
                        logger.debug(
                            "Stabiliser could not extract JSON tool calls from response",
                            exc_info=True,
                        )

        # ---- Original reasoning_content extraction ----
        chat_response = super()._parse_response_from_openai(response, options)
        for choice, msg in zip(response.choices, chat_response.messages):
            rc = getattr(choice.message, "reasoning_content", None)
            if rc:
                msg.contents.append(
                    Content.from_text_reasoning(
                        text=rc,
                        protected_data=json.dumps(rc),
                    )
                )
                # ALSO store the raw reasoning in additional_properties so
                # _prepare_message_for_openai can read it back without
                # relying on Content.protected_data (which MAF may corrupt).
                msg.additional_properties["deepseek_reasoning"] = rc
        return chat_response

    def _parse_response_update_from_openai(
        self,
        chunk: ChatCompletionChunk,
    ) -> Any:  # ChatResponseUpdate
        """Extract ``reasoning_content`` from streaming chunk.

        Accumulates per-delta reasoning into a single Content item rather
        than creating one Content per chunk, which would cause the base
        class to only preserve the last delta.
        """
        update = super()._parse_response_update_from_openai(chunk)
        for choice in chunk.choices:
            rc_delta = getattr(choice.delta, "reasoning_content", None)
            if rc_delta:
                update.contents.append(
                    Content.from_text_reasoning(
                        text=rc_delta,
                        protected_data=json.dumps(rc_delta),
                    )
                )
        return update

    def _prepare_message_for_openai(
        self,
        message: Message,
    ) -> list[dict[str, Any]]:
        """Convert reasoning back to ``reasoning_content`` for DeepSeek.

        Uses a priority chain to reconstruct the full reasoning text:

        1. ``message.additional_properties["deepseek_reasoning"]`` — the
           raw string stored by _parse_response_from_openai (avoids MAF
           Content.protected_data corruption).  Only set in non-streaming.

        2. Concatenate ``text`` from all ``text_reasoning`` Content items
           in the message.  This works for streaming (multiple per-delta
           Contents) where protected_data may be corrupted.

        3. ``reasoning_details`` set by the base class from Content
           ``protected_data`` (fallback — subject to corruption but kept
           for compatibility).
        """
        # ---- Reconstruct reasoning text from Content.text ----
        # The .text field survives MAF processing correctly; .protected_data
        # can be corrupted (especially for streaming per-delta Contents
        # where only the last delta's protected_data is preserved).
        raw_reasoning = message.additional_properties.get("deepseek_reasoning")
        if not raw_reasoning:
            reasoning_parts = [
                ct.text
                for ct in message.contents
                if ct.type == "text_reasoning" and ct.text
            ]
            if reasoning_parts:
                raw_reasoning = "".join(reasoning_parts)

        # ---- Build the message dict via the base class ----
        prepared = super()._prepare_message_for_openai(message)

        if raw_reasoning:
            # Inject reasoning_content into the assistant message.
            # Must remove any reasoning_details the base class may have
            # set (could be corrupted).
            for msg in prepared:
                if msg.get("role") == "assistant":
                    msg.pop("reasoning_details", None)
                    msg["reasoning_content"] = raw_reasoning
            return prepared

        # ---- Fallback: convert base class reasoning_details ----
        for msg in prepared:
            if "reasoning_details" in msg:
                value = msg.pop("reasoning_details")
                if isinstance(value, str):
                    try:
                        msg["reasoning_content"] = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        msg["reasoning_content"] = value
                else:
                    msg["reasoning_content"] = value
        return prepared


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_deepseek_client(
    model: Model,
    thinking_enabled: bool = False,
    reasoning_effort: str | None = None,
) -> DeepSeekThinkingClient:
    """Build a DeepSeek chat client from a Model record.

    DeepSeek exposes an OpenAI-compatible Chat Completions API, so we use
    OpenAIChatCompletionClient with a custom base_url.
    Appends /v1 if not already present.
    Raises ValueError if base_url is not set.
    """
    if not model.base_url:
        raise ValueError("DeepSeek provider requires a base_url")
    base_url = model.base_url.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"

    # Pre-configure an AsyncOpenAI client with timeout and retry settings
    # to survive transient network errors during long multi-tool streaming runs.
    import openai

    openai_client = openai.AsyncOpenAI(
        api_key=model.api_key,
        base_url=base_url,
        max_retries=2,
        timeout=900.0,
    )

    return DeepSeekThinkingClient(
        model=model.model_id or model.name,
        async_client=openai_client,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
    )
