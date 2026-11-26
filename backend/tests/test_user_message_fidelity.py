# =============================================================================
# PH Agent Hub — What the turn handed the model (Issue #661)
# =============================================================================
# A session can only show the *stored* user message; the model receives the
# contextualized string, and MAF can further compact it in-run.  These tests
# pin the turn's input digest to the exact string handed to ``agent.run(...)``:
#
#   * head *and* tail, because the observed loss was at the end of the message
#     — a head-only preview would have looked healthy three times;
#   * a sha256 of that exact string, so a pasted-in field, a contextualized
#     prefix or a future truncation is visible from an export alone;
#   * ``history_messages`` / ``contextualized``, so a replayed turn is
#     distinguishable from a fresh one;
#   * the fields ride inside the existing ``metrics`` part and therefore
#     survive the session export/import round trip.
# =============================================================================

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.runner import (
    _compute_turn_metrics,
    _count_history_messages,
    _maybe_summarize_and_build_context,
)
from src.agents.turn_input import (
    USER_MESSAGE_PREVIEW_CHARS,
    user_message_digest,
)

pytestmark = [pytest.mark.unit]


# The issue's message: two paragraphs, the second carrying the subject.
MESSAGE = (
    'Execute, in full, the prompt at "C:\\Users\\phalo\\source\\repos\\'
    'portfolio\\Prompts\\Stock Evaluation Report.md" for the company below. '
    "Do not start responding unless you read the whole prompt.\n"
    "\n"
    "Name:     Bank of America Corp\n"
    "Ticker:   BAC:xnys\n"
    "Currency: USD\n"
    "ISIN:     US0605051046"
)

SIX_KEYS = {
    "user_message_chars",
    "user_message_sha256",
    "user_message_head",
    "user_message_tail",
    "history_messages",
    "contextualized",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _metrics(**overrides) -> dict:
    kwargs = dict(
        turn_start_s=0.0,
        turn_end_s=1.0,
        first_token_at_s=None,
        tool_durations_s=[],
        steps=0,
        cache_hit_tokens=0,
        system_prompt_tokens=0,
        tool_definition_tokens=0,
        tokens_in=0,
    )
    kwargs.update(overrides)
    return _compute_turn_metrics(**kwargs)


def _mock_model():
    model = MagicMock()
    model.provider = "deepseek"
    model.model_id = "m1"
    model.max_tokens = 4096
    model.context_length = 128_000
    return model


def _text_update(text: str, finish_reason: str = "stop"):
    content = MagicMock()
    content.type = "text"
    content.text = text
    update = MagicMock()
    update.contents = [content]
    update.finish_reason = finish_reason
    return update


class TestDigest:
    def test_records_head_tail_chars_and_hash(self):
        digest = user_message_digest(MESSAGE)

        assert set(digest) == SIX_KEYS
        assert digest["user_message_chars"] == len(MESSAGE)
        assert digest["user_message_sha256"] == _sha(MESSAGE)
        assert digest["user_message_head"] == MESSAGE[:USER_MESSAGE_PREVIEW_CHARS]
        assert digest["user_message_tail"] == MESSAGE[-USER_MESSAGE_PREVIEW_CHARS:]
        # The whole point of the tail: the subject is at the end, so a cut at
        # the blank line fails here instead of failing a production run.
        assert MESSAGE.endswith("US0605051046")
        assert digest["user_message_tail"].endswith("US0605051046")
        assert "US0605051046" not in digest["user_message_head"]
        assert digest["history_messages"] == 0
        assert digest["contextualized"] is False

    def test_preview_is_bounded_but_hash_covers_the_whole_string(self):
        big = "A" * 100_000 + "\n\nISIN: US0605051046"
        digest = user_message_digest(big)

        assert digest["user_message_chars"] == len(big)
        assert len(digest["user_message_head"]) == USER_MESSAGE_PREVIEW_CHARS
        assert len(digest["user_message_tail"]) == USER_MESSAGE_PREVIEW_CHARS
        assert digest["user_message_sha256"] == _sha(big)
        assert digest["user_message_tail"].endswith("US0605051046")

    def test_short_message_head_and_tail_are_the_message(self):
        digest = user_message_digest("hello")

        assert digest["user_message_head"] == "hello"
        assert digest["user_message_tail"] == "hello"

    def test_missing_message_yields_nulls_not_an_exception(self):
        digest = user_message_digest(None, history_messages=2, contextualized=True)

        assert digest["user_message_chars"] is None
        assert digest["user_message_sha256"] is None
        assert digest["user_message_head"] is None
        assert digest["user_message_tail"] is None
        assert digest["history_messages"] == 2
        assert digest["contextualized"] is True

    def test_lone_surrogate_cannot_fail_a_turn(self):
        # A clipboard can carry an unpaired surrogate; telemetry must not raise.
        digest = user_message_digest("broken \ud800 text")

        assert digest["user_message_chars"] == len("broken \ud800 text")
        assert isinstance(digest["user_message_sha256"], str)


class TestMetricsFields:
    def test_six_fields_present_with_a_message(self):
        metrics = _metrics(
            tokens_in=1000,
            system_prompt_tokens=100,
            tool_definition_tokens=50,
            user_message=MESSAGE,
        )

        assert SIX_KEYS <= set(metrics)
        assert metrics["user_message_chars"] == len(MESSAGE)
        assert metrics["user_message_sha256"] == _sha(MESSAGE)
        assert metrics["user_message_tail"].endswith("US0605051046")
        assert metrics["contextualized"] is False

    def test_six_fields_present_without_a_message_and_without_tokens(self):
        """Every stored metrics part has the same shape (Issue #661)."""
        metrics = _metrics()

        assert SIX_KEYS <= set(metrics)
        assert metrics["user_message_chars"] is None
        assert metrics["user_message_sha256"] is None
        assert metrics["history_messages"] == 0
        assert metrics["contextualized"] is False

    def test_context_is_recorded_from_the_caller(self):
        metrics = _metrics(
            tokens_in=500,
            user_message=MESSAGE,
            history_messages=4,
            contextualized=True,
        )

        assert metrics["history_messages"] == 4
        assert metrics["contextualized"] is True


class TestComputeTurnMetricsDoesNotChangeExistingKeys:
    def test_existing_fields_still_computed(self):
        metrics = _metrics(
            turn_start_s=100.0,
            turn_end_s=101.5,
            first_token_at_s=100.1,
            tool_durations_s=[0.3, 0.2],
            steps=3,
            cache_hit_tokens=50,
            system_prompt_tokens=200,
            tool_definition_tokens=100,
            tokens_in=1000,
            user_message="hi",
        )

        assert metrics["llm_ms"] == 1000
        assert metrics["tool_ms"] == 500
        assert metrics["ttft_ms"] == 100
        assert metrics["messages_tokens"] == 700


class TestStreamingRunnerDigest:
    """The digest must be of the string the agent was actually given."""

    @pytest.mark.asyncio
    async def test_agent_receives_the_full_message_and_metrics_hash_it(self):
        from src.agents.runner import _run_agent_stream
        from src.agents.budget import RunBudget

        mock_instance = MagicMock()

        async def _stream():
            yield _text_update("done")

        mock_instance.run.return_value = _stream()
        token_counts: dict = {}
        with patch("agent_framework.Agent", return_value=mock_instance):
            async for _ in _run_agent_stream(
                model=_mock_model(),
                model_client=MagicMock(),
                system_prompt="test",
                tools=[],
                user_message=MESSAGE,
                agent_name="test",
                session_id="s1",
                message_id="m1",
                token_counts=token_counts,
                budget=RunBudget(max_steps=50),
            ):
                pass

        handed_to_agent = mock_instance.run.call_args[0][0]
        metrics = token_counts["metrics"]

        # The regression: a cut at the blank line fails right here.
        assert handed_to_agent == MESSAGE
        assert handed_to_agent.endswith("US0605051046")
        assert metrics["user_message_sha256"] == _sha(handed_to_agent)
        assert metrics["user_message_chars"] == len(MESSAGE)
        assert metrics["user_message_tail"].endswith("US0605051046")
        assert metrics["history_messages"] == 0
        assert metrics["contextualized"] is False

    @pytest.mark.asyncio
    async def test_nonstreaming_runner_records_the_digest(self):
        from src.agents.runner import _run_agent

        result = MagicMock()
        result.final_output = "done"
        result.usage_details = {}
        metrics_out: dict = {}
        ma = MagicMock()
        ma.run = AsyncMock(return_value=result)
        with patch("agent_framework.Agent", return_value=ma):
            await _run_agent(
                model=_mock_model(),
                model_client=MagicMock(),
                system_prompt="test",
                tools=[],
                user_message=MESSAGE,
                agent_name="test",
                metrics_out=metrics_out,
            )

        handed_to_agent = ma.run.call_args[0][0]
        assert handed_to_agent == MESSAGE
        assert metrics_out["user_message_sha256"] == _sha(handed_to_agent)
        assert metrics_out["user_message_tail"].endswith("US0605051046")
        assert metrics_out["contextualized"] is False


class TestContextStats:
    """``_maybe_summarize_and_build_context`` reports what it replayed."""

    def _cfg(self):
        cfg = MagicMock()
        cfg.model.context_length = None
        cfg.system_prompt = ""
        cfg.temperature = 0.7
        return cfg

    @pytest.mark.asyncio
    async def test_no_history_returns_the_message_unchanged(self):
        stats: dict = {}
        with patch(
            "src.agents.runner._get_messages_for_session",
            AsyncMock(return_value=[]),
        ):
            contextualized, _ = await _maybe_summarize_and_build_context(
                MagicMock(),
                {"id": "s1"},
                self._cfg(),
                MESSAGE,
                context_stats=stats,
            )

        assert contextualized == MESSAGE
        assert stats["contextualized"] is False
        assert stats["history_messages"] == 0
        # Acceptance: with no history the digest is the hash of the stored row.
        metrics = _metrics(tokens_in=10, user_message=contextualized, **stats)
        assert metrics["user_message_sha256"] == _sha(MESSAGE)

    @pytest.mark.asyncio
    async def test_history_replays_the_message_verbatim_and_says_so(self):
        history = [
            {
                "sender": "user",
                "content": [{"type": "text", "text": "previous question"}],
            },
            {
                "sender": "assistant",
                "content": [{"type": "text", "text": "previous answer"}],
            },
            {
                "sender": "user",
                "content": [{"type": "text", "text": MESSAGE}],
            },
        ]
        stats: dict = {}
        with patch(
            "src.agents.runner._get_messages_for_session",
            AsyncMock(return_value=history),
        ):
            contextualized, _ = await _maybe_summarize_and_build_context(
                MagicMock(),
                {"id": "s1"},
                self._cfg(),
                MESSAGE,
                context_stats=stats,
            )

        assert stats["contextualized"] is True
        assert stats["history_messages"] == 3
        # Acceptance: the message appears verbatim inside the replayed string.
        assert MESSAGE in contextualized
        assert contextualized.endswith(f"Current message: {MESSAGE}")

        metrics = _metrics(tokens_in=10, user_message=contextualized, **stats)
        # The digest is of the agent input, not of the stored row, and the
        # subject still ends the turn input.
        assert metrics["user_message_sha256"] != _sha(MESSAGE)
        assert metrics["user_message_tail"].endswith("US0605051046")
        assert metrics["history_messages"] == 3
        assert metrics["contextualized"] is True

    def test_count_skips_summarized_and_empty_messages(self):
        messages = [
            {"sender": "user", "content": [{"type": "text", "text": "kept"}]},
            {
                "sender": "user",
                "content": [{"type": "text", "text": "old"}],
                "summarized": True,
            },
            {"sender": "assistant", "content": []},
            {"sender": "assistant", "content": None},
            {"sender": "system", "content": [{"type": "text", "text": "summary"}]},
        ]
        assert _count_history_messages(messages) == 2


class TestExportRoundTrip:
    """The digest rides inside ``content`` — no export/import change needed."""

    def _message(self):
        digest = user_message_digest(MESSAGE, history_messages=0, contextualized=False)
        return {
            "sender": "assistant",
            "content": [
                {"type": "text", "text": "answer"},
                {"type": "metrics", **digest},
            ],
            "model_name": "deepseek",
            "model_provider": "deepseek",
            "tokens_in": 10,
            "tokens_out": 2,
            "created_at": "2026-11-26T12:00:00+00:00",
        }

    def test_export_carries_the_six_fields(self):
        from src.api.chat import _build_export_dict

        payload = _build_export_dict({"title": "T"}, [self._message()])
        exported = payload["messages"][0]["content"]
        metrics = next(p for p in exported if p.get("type") == "metrics")

        assert metrics["user_message_sha256"] == _sha(MESSAGE)
        assert metrics["user_message_tail"].endswith("US0605051046")
        # The export remains plain JSON.
        json.dumps(payload, default=str)

    def test_export_with_the_fields_imports(self):
        from src.api.chat import _validate_import_payload

        payload = {
            "version": 1,
            "session": {"title": "T"},
            "messages": [self._message()],
        }
        title, messages = _validate_import_payload(payload)

        assert title == "T"
        metrics = next(
            p for p in messages[0]["content"] if p.get("type") == "metrics"
        )
        assert set(metrics) >= SIX_KEYS

    def test_older_export_without_the_fields_still_imports(self):
        from src.api.chat import _validate_import_payload

        payload = {
            "version": 1,
            "session": {"title": "Old"},
            "messages": [
                {
                    "sender": "assistant",
                    "content": [
                        {"type": "text", "text": "answer"},
                        {"type": "metrics", "llm_ms": 10, "steps": 1},
                    ],
                }
            ],
        }
        title, messages = _validate_import_payload(payload)

        assert title == "Old"
        assert len(messages) == 1


# =============================================================================
# Issue #661 — the hub must never hand the model a conversation without the
# user's request.  MAF's budget fallback keeps only the last non-system group,
# so a tool-heavy turn can lose the request in-run while the transcript keeps
# it; the wrap-up call had the same problem via a 2,000-character excerpt of
# the contextualized prompt.
# =============================================================================


def _tool_heavy_conversation(
    n_rounds: int = 10,
    result_chars: int = 40_000,
    system_chars: int = 40_000,
    reasoning_chars: int = 120_000,
):
    """A turn shaped like the one in the issue: many big tool results."""
    from agent_framework import Content, Message

    messages = [Message("system", ["s" * system_chars]), Message("user", [MESSAGE])]
    for i in range(n_rounds):
        messages.append(Message("assistant", [
            Content(type="text_reasoning", text="r" * reasoning_chars),
            Content(
                type="function_call",
                name="PowerShell",
                call_id=f"c{i}",
                arguments={"command": "Get-Content x -Raw"},
            ),
        ]))
        messages.append(Message("tool", [
            Content(
                type="function_result",
                call_id=f"c{i}",
                name="PowerShell",
                result="y" * result_chars,
            )
        ]))
    return messages


class TestCompactionGuard:
    @pytest.mark.asyncio
    async def test_plain_budget_fallback_can_drop_the_user_message(self):
        """Documents the MAF behaviour the guard defends against."""
        from agent_framework import (
            CharacterEstimatorTokenizer,
            ToolResultCompactionStrategy,
            TokenBudgetComposedStrategy,
            apply_compaction,
        )

        tokenizer = CharacterEstimatorTokenizer()
        plain = TokenBudgetComposedStrategy(
            token_budget=89_600,  # 70 % of a 128K window
            tokenizer=tokenizer,
            strategies=[ToolResultCompactionStrategy(keep_last_tool_call_groups=3)],
            early_stop=True,
        )
        projected = await apply_compaction(
            _tool_heavy_conversation(), strategy=plain, tokenizer=tokenizer,
        )

        assert not any((m.text or "") == MESSAGE for m in projected)

    @pytest.mark.asyncio
    async def test_hub_strategy_keeps_the_user_message_and_says_it_compacted(self):
        from agent_framework import apply_compaction, CharacterEstimatorTokenizer

        from src.agents.runner import _build_compaction_strategy
        from src.agents.turn_input import TurnInputPreservingCompaction

        strategy, tokenizer = _build_compaction_strategy(
            _mock_model(), MagicMock(), [MagicMock()],
        )
        assert isinstance(strategy, TurnInputPreservingCompaction)

        projected = await apply_compaction(
            _tool_heavy_conversation(), strategy=strategy, tokenizer=tokenizer,
        )

        # The turn's request survives, and the turn records that it compacted.
        assert any((m.text or "") == MESSAGE for m in projected)
        assert "US0605051046" in "".join(m.text or "" for m in projected)
        assert strategy.compacted_rounds >= 1

    @pytest.mark.asyncio
    async def test_no_tools_means_no_compaction_strategy(self):
        from src.agents.runner import _build_compaction_strategy

        assert _build_compaction_strategy(_mock_model(), MagicMock(), []) == (None, None)

    def test_metrics_carry_context_compacted(self):
        metrics = _metrics(tokens_in=10, user_message=MESSAGE, context_compacted=True)

        assert metrics["context_compacted"] is True
        assert _metrics(tokens_in=10, user_message=MESSAGE)["context_compacted"] is False

    def test_clean_turn_has_no_stream_error(self):
        assert _metrics(tokens_in=10)["stream_error"] is None

    def test_stream_error_is_recorded_verbatim(self):
        error = {"type": "RuntimeError", "message": "connection reset by peer", "code": "internal_error"}
        metrics = _metrics(tokens_in=10, stream_error=error)

        assert metrics["stream_error"] == error


class TestStopWrapUpRequest:
    """The early-stop conclusion must be about the user's actual request."""

    def _client(self, text: str = "conclusion"):
        client = MagicMock()
        response = MagicMock()
        response.messages = [MagicMock(text=text)]
        client.get_response = AsyncMock(return_value=response)
        return client

    def _instruction(self, client) -> str:
        payload = client.get_response.call_args.kwargs
        return payload["messages"][1].text

    @pytest.mark.asyncio
    async def test_uses_the_original_request_not_a_contextualized_prefix(self):
        from src.agents.runner import _generate_stop_wrapup

        client = self._client()
        contextualized = (
            "[Previous conversation]\n\n"
            "User: earlier question\n\n"
            "Assistant: " + ("prior answer " * 500) + "\n\n---\n\n"
            f"Current message: {MESSAGE}"
        )
        await _generate_stop_wrapup(
            model_client=client,
            system_prompt="sys",
            user_message=contextualized,
            turn_notes=["Get-Content x -Raw"],
            stop_reason="no_answer",
            steps=10,
            tool_calls=10,
            original_request=MESSAGE,
        )

        instruction = self._instruction(client)
        assert MESSAGE in instruction
        assert "US0605051046" in instruction
        assert "[Previous conversation]" not in instruction

    @pytest.mark.asyncio
    async def test_a_huge_request_keeps_both_ends(self):
        from src.agents.runner import _generate_stop_wrapup

        client = self._client()
        huge = "BEGINNING ISO 9001\n" + ("x" * 90_000) + "\nENDING ISIN: US0605051046"
        await _generate_stop_wrapup(
            model_client=client,
            system_prompt="sys",
            user_message=huge,
            turn_notes=[],
            stop_reason="no_answer",
            steps=1,
            original_request=huge,
        )

        instruction = self._instruction(client)
        # Head and tail both survive; the middle is marked as dropped.
        assert "BEGINNING ISO 9001" in instruction
        assert "ENDING ISIN: US0605051046" in instruction
        assert "truncated" in instruction
