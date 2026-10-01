# =============================================================================
# PH Agent Hub — Current Session Context Tests (Issue #572)
# =============================================================================
# The agent is told which session it is running in:
#   1. a ``## Current Session`` block in the system prompt (id + canonical URL)
#   2. the same block appended to every workflow step's instructions
#   3. ``session_id`` / ``session_url`` seeded into ``function_invocation_kwargs``
#      so a writer tool can stamp provenance without the model retyping the link
#   4. ``{{SESSION_ID}}`` / ``{{SESSION_URL}}`` substitution in admin templates
# =============================================================================

import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.runner import (
    _build_session_context_block,
    _build_system_prompt,
    _seed_invocation_kwargs,
    _session_url,
    _substitute_session_placeholders,
)

FRONTEND = "https://hub.example.com"


def _session(**overrides) -> dict:
    """A minimal permanent-session dict owned by a real user."""
    data = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "is_temporary": False,
    }
    data.update(overrides)
    return data


@pytest.fixture
def frontend_url(monkeypatch):
    """Pin ``FRONTEND_URL`` so URL assertions are deterministic."""
    monkeypatch.setattr(
        "src.core.config.settings.FRONTEND_URL", FRONTEND, raising=True
    )
    return FRONTEND


# ===========================================================================
# Canonical URL shape
# ===========================================================================


@pytest.mark.unit
class TestSessionUrl:
    """``_session_url`` only returns a link that can actually resolve."""

    def test_permanent_session_url(self, frontend_url):
        data = _session()
        assert _session_url(data) == f"{FRONTEND}/chat/{data['id']}"

    def test_temporary_session_still_gets_url(self, frontend_url):
        """Temp sessions are reachable until they expire — the link is real."""
        data = _session(is_temporary=True)
        assert _session_url(data) == f"{FRONTEND}/chat/{data['id']}"

    def test_widget_guest_owner_has_no_url(self, frontend_url):
        """Widget visitors are ``guest:<embed_config_id>`` — no account to sign in."""
        data = _session(user_id="guest:embed-config-1")
        assert _session_url(data) is None

    def test_demo_owner_has_no_url(self, frontend_url):
        """Demo mode owners are ``demo:<tenant_id>``."""
        data = _session(user_id="demo:tenant-1")
        assert _session_url(data) is None

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_unconfigured_frontend_url(self, monkeypatch, value):
        monkeypatch.setattr(
            "src.core.config.settings.FRONTEND_URL", value, raising=True
        )
        assert _session_url(_session()) is None

    def test_missing_session_id(self, frontend_url):
        assert _session_url({"user_id": "u1"}) is None

    def test_trailing_slash_is_normalised(self, monkeypatch):
        monkeypatch.setattr(
            "src.core.config.settings.FRONTEND_URL",
            f"{FRONTEND}/",
            raising=True,
        )
        data = _session()
        assert _session_url(data) == f"{FRONTEND}/chat/{data['id']}"


# ===========================================================================
# System prompt block
# ===========================================================================


@pytest.mark.unit
class TestSessionContextBlock:
    """The block text itself."""

    def test_contains_id_and_url(self, frontend_url):
        data = _session()
        block = _build_session_context_block(data)
        assert block is not None
        assert "## Current Session" in block
        assert data["id"] in block
        assert f"{FRONTEND}/chat/{data['id']}" in block

    def test_only_the_owning_session_id_appears(self, frontend_url):
        """No sibling session's id leaks into the block."""
        other = _session()
        data = _session()
        block = _build_session_context_block(data)
        assert data["id"] in block
        assert other["id"] not in block

    def test_no_id_no_block(self, frontend_url):
        assert _build_session_context_block({"user_id": "u1"}) is None

    def test_temporary_session_is_marked_non_durable(self, frontend_url):
        block = _build_session_context_block(_session(is_temporary=True))
        assert "temporary session" in block
        assert "durable link" in block

    def test_guest_session_states_url_unavailable(self, frontend_url):
        block = _build_session_context_block(_session(user_id="guest:cfg-1"))
        assert "/chat/" not in block
        assert "unavailable" in block
        assert "Do not invent" in block

    def test_url_unavailable_when_frontend_unconfigured(self, monkeypatch):
        monkeypatch.setattr(
            "src.core.config.settings.FRONTEND_URL", "", raising=True
        )
        block = _build_session_context_block(_session())
        assert "/chat/" not in block
        assert "unavailable" in block


@pytest.mark.integration
class TestSessionBlockInSystemPrompt:
    """``_build_system_prompt`` injects the block after the platform identity."""

    @patch(
        "src.agents.runner._AGENT_IDENTITY",
        "## Platform Identity\n\nTest identity.",
    )
    async def test_block_present_and_ordered(
        self, db_session, test_tenant, test_user, frontend_url
    ):
        data = _session(tenant_id=test_tenant.id, user_id=test_user.id)
        prompt = await _build_system_prompt(db_session, data, user=None)

        assert "## Current Session" in prompt
        assert f"{FRONTEND}/chat/{data['id']}" in prompt
        # Salience: the run facts sit directly after the platform identity.
        assert prompt.index("## Platform Identity") < prompt.index(
            "## Current Session"
        )

    @patch(
        "src.agents.runner._AGENT_IDENTITY",
        "## Platform Identity\n\nTest identity.",
    )
    async def test_id_and_url_stable_across_turns(
        self, db_session, test_tenant, test_user, frontend_url
    ):
        """The value must not drift between turns of the same session."""
        data = _session(tenant_id=test_tenant.id, user_id=test_user.id)
        first = await _build_system_prompt(db_session, data, user=None)
        second = await _build_system_prompt(db_session, data, user=None)
        assert first == second

    async def test_no_block_without_session_id(
        self, db_session, test_tenant, test_user, frontend_url
    ):
        prompt = await _build_system_prompt(
            db_session,
            {"tenant_id": test_tenant.id, "user_id": test_user.id},
            user=None,
        )
        assert "## Current Session" not in prompt


# ===========================================================================
# Template placeholder substitution
# ===========================================================================


@pytest.mark.unit
class TestPlaceholderSubstitution:
    """``{{SESSION_ID}}`` / ``{{SESSION_URL}}`` in admin templates."""

    def test_substitutes_both(self, frontend_url):
        data = _session()
        out = _substitute_session_placeholders(
            "id={{SESSION_ID}} url={{SESSION_URL}}", data
        )
        assert out == f"id={data['id']} url={FRONTEND}/chat/{data['id']}"

    def test_url_collapses_to_marker_when_unresolvable(self, frontend_url):
        out = _substitute_session_placeholders(
            "url={{SESSION_URL}}", _session(user_id="guest:cfg-1")
        )
        assert "{{SESSION_URL}}" not in out
        assert "no resolvable link" in out

    def test_text_without_placeholders_untouched(self, frontend_url):
        assert (
            _substitute_session_placeholders("plain prompt", _session())
            == "plain prompt"
        )


# ===========================================================================
# Tool-invocation provenance (no-hallucination path)
# ===========================================================================


@pytest.mark.unit
class TestSeedInvocationKwargs:
    """``_seed_invocation_kwargs`` seeds the values tools read from ``ctx.kwargs``."""

    def test_seeds_all_three(self, frontend_url):
        data = _session()
        kwargs = _seed_invocation_kwargs(None, data)
        assert kwargs["session_data"] is data
        assert kwargs["session_id"] == data["id"]
        assert kwargs["session_url"] == f"{FRONTEND}/chat/{data['id']}"

    def test_omits_url_when_unresolvable(self, frontend_url):
        kwargs = _seed_invocation_kwargs(None, _session(user_id="guest:cfg-1"))
        assert kwargs["session_id"]  # id is always seeded
        assert "session_url" not in kwargs

    def test_does_not_clobber_caller_values(self, frontend_url):
        data = _session()
        existing = {"session_id": "caller-supplied"}
        kwargs = _seed_invocation_kwargs(existing, data)
        assert kwargs is existing
        assert kwargs["session_id"] == "caller-supplied"

    def test_mutates_and_returns_the_same_dict(self, frontend_url):
        data = _session()
        kwargs = {}
        assert _seed_invocation_kwargs(kwargs, data) is kwargs


@pytest.mark.unit
class TestCtxKwargsCannotBeSpoofed:
    """A tool reading ``ctx.kwargs`` gets the platform value, not the model's.

    This is the property that lets a downstream writer stamp provenance without
    trusting the model to retype a 36-character session URL.
    """

    async def test_model_argument_does_not_reach_ctx_kwargs(self, frontend_url):
        from agent_framework import FunctionInvocationContext, tool

        @tool
        async def probe_writer(
            isin: str,
            session_url: str = "",
            ctx: FunctionInvocationContext | None = None,
        ) -> dict:
            """Probe writer tool that stamps provenance from its context."""
            return {
                "isin": isin,
                "model_supplied": session_url,
                "platform": ctx.kwargs.get("session_url") if ctx else None,
            }

        data = _session()
        seeded = _seed_invocation_kwargs(None, data)
        expected = f"{FRONTEND}/chat/{data['id']}"

        from agent_framework import FunctionInvocationContext as FIC

        context = FIC(
            function=probe_writer,
            arguments={"isin": "X"},
            kwargs=dict(seeded),
        )
        raw = await probe_writer.invoke(
            arguments={"isin": "X", "session_url": "http://hallucinated.invalid/x"},
            context=context,
            skip_parsing=True,
        )

        assert raw["platform"] == expected
        assert raw["model_supplied"] != raw["platform"]


# ===========================================================================
# Workflow step agents (Issue #572, Option B)
# ===========================================================================


@pytest.mark.unit
class TestWorkflowStepSessionContext:
    """Step agents get the block appended to their own instructions."""

    @pytest.fixture(autouse=True)
    def _stub_reference_validation(self, monkeypatch):
        """Skip execute-time reference validation (needs real tenant rows)."""
        monkeypatch.setattr(
            "src.services.workflow_reference_service.assert_definition_references",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "src.services.workflow_definition_resolver.ensure_definition_enabled",
            AsyncMock(return_value=None),
        )

    @staticmethod
    def _defn():
        from src.agents.workflows.definition import WorkflowDefinition

        return WorkflowDefinition(
            key="session_context_probe",
            name="Session Context Probe",
            steps=[
                {
                    "id": "step1",
                    "name": "Step1",
                    "type": "inline",
                    "instructions": "Do the thing.",
                },
                {
                    "id": "step2",
                    "name": "Step2",
                    "type": "inline",
                    "instructions": "Report the thing.",
                },
            ],
        )

    async def _build(self, session_context):
        from src.agents.workflows.engine import build_workflow

        with patch(
            "src.agents.workflows.engine.resolve_model", new=AsyncMock()
        ), patch(
            "src.agents.workflows.engine._build_agent_for_step"
        ) as mock_build:
            mock_build.return_value = MagicMock()
            await build_workflow(
                defn=self._defn(),
                db=MagicMock(),
                tenant_id="T1",
                extra_tools=None,
                session_context=session_context,
            )
        return [
            call.kwargs["instructions"] for call in mock_build.call_args_list
        ]

    async def test_block_appended_to_every_step(self, frontend_url):
        data = _session()
        block = _build_session_context_block(data)
        instructions = await self._build(block)

        assert len(instructions) == 2
        assert all(block in text for text in instructions)
        # Every step sees the same session id and URL.
        assert all(data["id"] in text for text in instructions)
        assert all(f"{FRONTEND}/chat/{data['id']}" in text for text in instructions)
        # The authored instructions are preserved and come first.
        assert instructions[0].startswith("Do the thing.")
        assert instructions[1].startswith("Report the thing.")

    async def test_guest_session_step_gets_unavailable_notice(self, frontend_url):
        block = _build_session_context_block(_session(user_id="guest:cfg-1"))
        instructions = await self._build(block)
        assert all("/chat/" not in text for text in instructions)
        assert all("unavailable" in text for text in instructions)

    async def test_no_context_leaves_instructions_unchanged(self, frontend_url):
        instructions = await self._build(None)
        assert instructions[0] == "Do the thing."
        assert instructions[1] == "Report the thing."
