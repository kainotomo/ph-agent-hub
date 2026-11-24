# =============================================================================
# PH Agent Hub — SerializedSession tests (Issue #634)
# =============================================================================
# The proxy must serialize the awaitable AsyncSession operations of one agent
# turn, pass sync members through, and expose rollback_if_failed so a poisoned
# shared session can be healed mid-run.
# =============================================================================

import asyncio

import pytest

from src.db.serialized import SerializedSession

pytestmark = pytest.mark.unit


class _FakeSession:
    """Records overlapping awaits; mimics is_active/rollback/add."""

    def __init__(self, active: bool = True) -> None:
        self.active = active
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls: list[str] = []
        self.commits = 0
        self.fail_next: Exception | None = None

    @property
    def is_active(self) -> bool:
        return self.active

    async def _op(self, name: str, delay: float = 0.005) -> None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.calls.append(name)
        try:
            await asyncio.sleep(delay)
            if self.fail_next is not None:
                exc, self.fail_next = self.fail_next, None
                raise exc
        finally:
            self.in_flight -= 1

    async def execute(self, *_args, **_kwargs):
        await self._op("execute")
        return "result"

    async def commit(self):
        await self._op("commit")
        self.commits += 1

    async def rollback(self):
        self.calls.append("rollback")
        self.active = True

    def add(self, _obj) -> None:
        self.calls.append("add")


async def test_concurrent_operations_never_overlap():
    fake = _FakeSession()
    proxy = SerializedSession(fake)

    await asyncio.gather(
        *[proxy.execute() for _ in range(10)],
        *[proxy.commit() for _ in range(5)],
    )

    assert fake.max_in_flight == 1
    assert fake.commits == 5
    assert fake.calls.count("execute") == 10


async def test_a_failing_operation_releases_the_lock():
    fake = _FakeSession()
    fake.fail_next = RuntimeError("flush failed")
    proxy = SerializedSession(fake)

    with pytest.raises(RuntimeError):
        await proxy.execute()

    # The lock must have been released: the next operation still runs.
    assert await proxy.execute() == "result"


async def test_sync_members_pass_through():
    fake = _FakeSession()
    proxy = SerializedSession(fake)

    proxy.add(object())

    assert fake.calls == ["add"]
    assert proxy.is_active is True
    assert proxy.raw is fake


async def test_rollback_if_failed_rolls_back_an_inactive_session():
    fake = _FakeSession(active=False)
    proxy = SerializedSession(fake)

    assert await proxy.rollback_if_failed() is True
    assert "rollback" in fake.calls


async def test_rollback_if_failed_is_a_noop_for_an_active_session():
    fake = _FakeSession(active=True)
    proxy = SerializedSession(fake)

    assert await proxy.rollback_if_failed() is False
    assert "rollback" not in fake.calls