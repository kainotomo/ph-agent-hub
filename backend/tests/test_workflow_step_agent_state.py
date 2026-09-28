"""Tests for StepAgent state persistence in AgentSession.

These tests verify that StepAgent stores per-step state (outputs, user_message)
in the MAF AgentSession.state dict so it is checkpoint-visible, while falling
back to the legacy shared dict when no session is provided.
"""

import pytest

from agent_framework import AgentResponse, AgentResponseUpdate, Content, Message
from agent_framework._sessions import AgentSession

from src.agents.workflows.executors import StepAgent


# =============================================================================
# Recording stub agent (module-level)
# =============================================================================


class RecordingAgent:
    """Minimal agent that records every message list it receives."""

    def __init__(self, agent_id: str, response_text: str) -> None:
        self.id = agent_id
        self.name = agent_id
        self.description = None
        self.response_text = response_text
        self.seen: list[list[tuple[str, str]]] = []

    def create_session(self):
        return AgentSession()

    def run(self, messages=None, *, stream=False, session=None, **kwargs):
        rec = [(m.role, m.text) for m in (messages or [])]
        self.seen.append(rec)

        if stream:

            async def _gen():
                yield AgentResponseUpdate(
                    contents=[Content(type="text", text=self.response_text)]
                )

            return _gen()
        else:

            async def _coro():
                return AgentResponse(
                    messages=[Message("assistant", [self.response_text])]
                )

            return _coro()


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.unit
class TestStepAgentStatePersistence:

    async def test_state_is_written_to_session(self):
        """StepAgent writes outputs and user_message into AgentSession.state."""
        inner = RecordingAgent("a", "OUT-A")
        agent = StepAgent(
            inner=inner, step_id="a", input_spec="", shared={}
        )
        session = AgentSession()

        await agent.run([Message("user", ["HELLO"])], session=session)

        assert session.state["outputs"]["a"] == "OUT-A"
        assert session.state["user_message"] == "HELLO"

    async def test_session_state_survives_round_trip(self):
        """State persisted in AgentSession.state survives to_dict/from_dict."""
        inner = RecordingAgent("a", "OUT-A")
        agent = StepAgent(
            inner=inner, step_id="a", input_spec="", shared={}
        )
        session = AgentSession()

        await agent.run([Message("user", ["HELLO"])], session=session)

        restored = AgentSession.from_dict(session.to_dict())
        assert restored.state["outputs"]["a"] == "OUT-A"
        assert restored.state["user_message"] == "HELLO"

    async def test_falls_back_to_shared_when_no_session(self):
        """When no session is passed, state goes into the shared dict."""
        inner = RecordingAgent("a", "OUT-A")
        shared = {}
        agent = StepAgent(
            inner=inner, step_id="a", input_spec="", shared=shared
        )

        await agent.run([Message("user", ["HELLO"])])

        assert shared["outputs"]["a"] == "OUT-A"
        assert shared["user_message"] == "HELLO"

    async def test_output_of_reads_session_state(self):
        """output_of:<step_id> reads from AgentSession.state['outputs']."""
        inner = RecordingAgent("b", "OUT-B")

        session = AgentSession()
        session.state["outputs"] = {"a": "FROM-A"}

        agent = StepAgent(
            inner=inner, step_id="b", input_spec="output_of:a", shared={}
        )
        await agent.run(
            [Message("assistant", ["upstream"])],
            session=session,
        )

        # The inner agent should have received the resolved output_of:a value
        assert ("user", "FROM-A") in inner.seen[0]

    async def test_user_message_is_not_overwritten(self):
        """user_message set before run is preserved (setdefault does not overwrite)."""
        inner = RecordingAgent("b", "OUT-B")

        session = AgentSession()
        session.state["user_message"] = "ORIGINAL"

        agent = StepAgent(
            inner=inner, step_id="b", input_spec="user_message", shared={}
        )
        await agent.run(
            [Message("assistant", ["upstream"])],
            session=session,
        )

        assert session.state["user_message"] == "ORIGINAL"
        assert ("user", "ORIGINAL") in inner.seen[0]
