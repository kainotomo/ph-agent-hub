# =============================================================================
# PH Agent Hub — Runner Workflow Checkpoint Wiring Test
# =============================================================================
# Verifies that _run_workflow_stream constructs a tenant/session/message-bound
# MariaDBCheckpointStorage and passes it downstream to build_workflow and
# iter_workflow_sse.
# =============================================================================

import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.unit
async def test_run_workflow_stream_constructs_checkpoint_storage():
    """Streaming path creates a MariaDBCheckpointStorage bound to tenant,
    session, and message, and passes the same sentinel to build_workflow
    and iter_workflow_sse."""

    # --- fake iter_workflow_sse: captures kwargs then returns empty async gen ---
    captured_kwargs = {}

    async def fake_iter_workflow_sse(**kwargs):
        captured_kwargs.update(kwargs)
        if False:
            yield  # makes this an async generator

    # --- build stubs and skill ---
    storage_cls = MagicMock()
    build_workflow_mock = AsyncMock(return_value=MagicMock())

    skill = types.SimpleNamespace(
        maf_target_key="wf",
        tenant_id="T1",
        default_model_id=None,
    )

    db = MagicMock()

    # --- patch targets and call ---
    with patch(
        "src.agents.workflows.checkpoint_storage.MariaDBCheckpointStorage",
        storage_cls,
    ), patch(
        "src.agents.workflows.engine.build_workflow",
        build_workflow_mock,
    ), patch(
        "src.agents.workflows.engine.iter_workflow_sse",
        fake_iter_workflow_sse,
    ), patch(
        "src.agents.workflows.engine.load_workflow_definition",
        MagicMock(return_value=MagicMock()),
    ), patch(
        "src.agents.registry.get_registered",
        MagicMock(return_value=MagicMock()),
    ):
        from src.agents.runner import _run_workflow_stream

        events = [
            e
            async for e in _run_workflow_stream(
                model=MagicMock(),
                skill=skill,
                model_client=MagicMock(),
                system_prompt="",
                tools=[],
                user_message="hi",
                agent_name="a",
                session_id="S1",
                message_id="M1",
                db=db,
            )
        ]

    # --- assertions ---
    assert storage_cls.call_args.kwargs["tenant_id"] == "T1"
    assert storage_cls.call_args.kwargs["session_id"] == "S1"
    assert storage_cls.call_args.kwargs["message_id"] == "M1"
    assert (
        build_workflow_mock.call_args.kwargs["checkpoint_storage"]
        is storage_cls.return_value
    )
    assert (
        captured_kwargs["checkpoint_storage"] is storage_cls.return_value
    )
