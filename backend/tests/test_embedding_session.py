# =============================================================================
# PH Agent Hub — background embedding owns and closes its DB session (#634)
# =============================================================================
# The embedding task used to receive a session created by the caller and never
# closed it; on any error the connection stayed checked out and the pool
# leaked, which is one way a long chat could end up unable to persist.
# =============================================================================

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _session_stub() -> MagicMock:
    fake_db = MagicMock()
    fake_db.close = AsyncMock()
    return fake_db


async def test_embedding_task_closes_its_session_on_success():
    from src.agents.runner import _schedule_message_embedding

    fake_db = _session_stub()

    with patch(
        "src.agents.runner._fresh_db_session",
        new=AsyncMock(return_value=fake_db),
    ), patch(
        "src.services.embedding_service.embed_message",
        new=AsyncMock(return_value=None),
    ) as mock_embed:
        await _schedule_message_embedding(
            message_id="m1", user_id="u1", tenant_id="t1", text="hello",
        )

    mock_embed.assert_awaited_once()
    assert mock_embed.await_args.kwargs["db"] is fake_db
    fake_db.close.assert_awaited_once()


async def test_embedding_task_closes_its_session_on_failure():
    from src.agents.runner import _schedule_message_embedding

    fake_db = _session_stub()

    with patch(
        "src.agents.runner._fresh_db_session",
        new=AsyncMock(return_value=fake_db),
    ), patch(
        "src.services.embedding_service.embed_message",
        new=AsyncMock(side_effect=RuntimeError("embedding API down")),
    ):
        # Swallowed: a background embedding must never surface as a run
        # failure, but the session must still be closed.
        await _schedule_message_embedding(
            message_id="m1", user_id="u1", tenant_id="t1", text="hello",
        )

    fake_db.close.assert_awaited_once()
