"""In-memory work source: claim/lease/reclaim/stop contract."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blackbox.core.errors import WorkItemNotFoundError, WorkSourceError
from blackbox.workers import InMemoryWorkSource, WorkerCredentials, WorkResult, WorkSource

START = datetime(2026, 6, 12, 15, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def make_source() -> tuple[InMemoryWorkSource, FakeClock]:
    clock = FakeClock()
    return InMemoryWorkSource(clock=clock), clock


def test_satisfies_work_source_protocol() -> None:
    source, _ = make_source()
    assert isinstance(source, WorkSource)


async def test_claim_complete_lifecycle() -> None:
    source, _ = make_source()
    item = source.enqueue("sess_1", environment_id="env_1", metadata={"input": "s3://x"})
    assert source.status_for(item.id) == "queued"

    claimed = await source.claim()
    assert claimed is not None
    assert claimed.id == item.id
    assert claimed.session_id == "sess_1"
    assert claimed.environment_id == "env_1"
    assert claimed.metadata == {"input": "s3://x"}
    assert claimed.attempt == 1
    assert source.status_for(item.id) == "claimed"

    await source.complete(item.id, WorkResult(status="completed", detail="ok"))
    assert source.status_for(item.id) == "completed"
    result = source.result_for(item.id)
    assert result is not None and result.detail == "ok"


async def test_claim_is_fifo_and_empty_returns_none() -> None:
    source, _ = make_source()
    assert await source.claim() is None
    first = source.enqueue("sess_a")
    second = source.enqueue("sess_b")
    claimed_first = await source.claim()
    claimed_second = await source.claim()
    assert claimed_first is not None and claimed_first.id == first.id
    assert claimed_second is not None and claimed_second.id == second.id
    assert await source.claim() is None


async def test_reclaim_stale_lease_increments_attempt() -> None:
    source, clock = make_source()
    item = source.enqueue("sess_1")
    assert await source.claim() is not None

    # Within the lease horizon nothing is reclaimable.
    clock.advance(seconds=1)
    assert await source.claim(reclaim_older_than_ms=2000) is None

    # A heartbeat refreshes the lease.
    await source.heartbeat(item.id)
    clock.advance(seconds=1)
    assert await source.claim(reclaim_older_than_ms=2000) is None

    # Once the heartbeat goes stale the item is re-claimed with attempt + 1.
    clock.advance(seconds=3)
    reclaimed = await source.claim(reclaim_older_than_ms=2000)
    assert reclaimed is not None
    assert reclaimed.id == item.id
    assert reclaimed.attempt == 2


async def test_heartbeat_and_complete_require_claimed_state() -> None:
    source, _ = make_source()
    item = source.enqueue("sess_1")
    with pytest.raises(WorkSourceError):
        await source.heartbeat(item.id)
    with pytest.raises(WorkSourceError):
        await source.complete(item.id, WorkResult(status="completed"))
    with pytest.raises(WorkItemNotFoundError):
        await source.heartbeat("work_missing")


async def test_stop_request_on_queued_item_stops_immediately() -> None:
    source, _ = make_source()
    item = source.enqueue("sess_1")
    source.request_stop(item.id)
    assert source.status_for(item.id) == "stopped"
    assert await source.claim() is None


async def test_stop_request_on_claimed_item_sets_flag() -> None:
    source, _ = make_source()
    item = source.enqueue("sess_1")
    await source.claim()
    assert await source.stop_requested(item.id) is False
    source.request_stop(item.id, force=True)
    assert await source.stop_requested(item.id) is True


async def test_stats_reports_depth_pending_oldest_and_liveness() -> None:
    source, clock = make_source()
    empty = await source.stats()
    assert (empty.depth, empty.pending, empty.oldest_queued_at) == (0, 0, None)
    assert empty.workers_polling == 0  # no claim poll recorded yet

    first = source.enqueue("sess_a")
    clock.advance(minutes=1)
    source.enqueue("sess_b")
    await source.claim()

    stats = await source.stats()
    assert stats.depth == 1
    assert stats.pending == 1
    assert stats.oldest_queued_at is not None
    assert stats.workers_polling == 1
    assert first.enqueued_at is not None

    clock.advance(minutes=5)
    stale = await source.stats()
    assert stale.workers_polling == 0


def test_credentials_repr_excludes_environment_key() -> None:
    creds = WorkerCredentials(environment_id="env_1", environment_key="sk-ant-oat01-secret")
    assert "sk-ant-oat01-secret" not in repr(creds)
    assert "env_1" in repr(creds)
