# =============================================================================
# PH Agent Hub — Serialized AsyncSession proxy (Issue #634)
# =============================================================================
# Every DB-writing tool of a turn receives the same request-scoped
# ``AsyncSession``, while MAF runs a tool batch with ``asyncio.gather``.  Two
# coroutines using one AsyncSession concurrently leave it in
# ``PendingRollback`` ("Method 'commit()' can't be called here", "Usage of the
# 'Session.add()' operation is not currently supported within the execution
# stage of the flush process"), and every later operation on that session
# fails — which used to take the whole answer with it.
#
# ``SerializedSession`` is a thin proxy around one AsyncSession that runs its
# awaitable operations under a single asyncio.Lock, so a tool batch is
# serialized without touching any tool implementation.  Sync members (``add``,
# ``no_autoflush``, ``is_active``, ...) are passed through unchanged.
#
# ``rollback_if_failed`` lets the runner heal a session whose transaction
# already failed, so one swallowed tool error cannot poison the rest of the
# turn.
# =============================================================================

from __future__ import annotations

import asyncio
from typing import Any

# AsyncSession members that must never run concurrently on one session.
_AWAITABLE = frozenset({
    "execute",
    "commit",
    "flush",
    "refresh",
    "rollback",
    "close",
    "delete",
    "get",
    "scalar",
    "scalars",
    "merge",
})


class SerializedSession:
    """Proxy that serializes the awaitable operations of one AsyncSession."""

    def __init__(self, session: Any) -> None:
        self._session = session
        self._lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._session, name)
        if name not in _AWAITABLE:
            return attr

        async def _serialized(*args: Any, **kwargs: Any) -> Any:
            async with self._lock:
                return await attr(*args, **kwargs)

        return _serialized

    @property
    def is_active(self) -> bool:
        return bool(getattr(self._session, "is_active", True))

    @property
    def raw(self) -> Any:
        """The wrapped session (escape hatch for tests and diagnostics)."""
        return self._session

    async def rollback_if_failed(self) -> bool:
        """Roll back a session left in a failed transaction.

        Returns True when a rollback was issued.  Safe to call at any time.
        """
        async with self._lock:
            if self._session.is_active:
                return False
            await self._session.rollback()
            return True