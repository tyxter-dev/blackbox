"""Lab-neutral work-queue contract for environment workers.

An AI lab's managed-agent control plane enqueues sessions as **work items**;
a customer-side worker claims them, executes the session's tool calls inside
the customer boundary, and posts a result back. This module defines that
contract (:class:`WorkSource`) plus the value objects it exchanges, with
vocabulary copied from Anthropic's environments work API (claim/lease,
dead-worker reclaim, queue stats) so adapters map one-to-one. See
``docs/ENVIRONMENT_WORKERS.md`` for the full analysis.

Semantics:

- ``claim`` returns the next work item leased to this worker, or ``None``
  when the queue is empty. ``block_ms`` is a server-side long-poll hint
  (sources without one return immediately). ``reclaim_older_than_ms``
  re-claims items leased to a worker whose heartbeat has gone stale.
- ``heartbeat`` keeps a claimed item's lease alive while the handler runs.
  Sources whose platform manages the lease internally may make this a no-op.
- ``complete`` posts the terminal :class:`WorkResult` and releases the item.
- ``stop_requested`` reports whether the control plane asked this item to be
  interrupted; the worker cancels the in-flight handler when it turns true.

:class:`InMemoryWorkSource` is the reference implementation: it backs the
offline test suite and is the starting point for a customer-owned control
plane. Lab adapters live next to it (``blackbox.workers.anthropic``).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import uuid4

from blackbox.core.errors import WorkItemNotFoundError, WorkSourceError

WorkResultStatus = Literal["completed", "failed", "stopped", "skipped"]
WorkItemStatus = Literal["queued", "claimed", "completed", "failed", "stopped", "skipped"]

#: How recently a source must have been polled to count toward
#: ``WorkQueueStats.workers_polling`` (mirrors the platform's 30-second window).
WORKERS_POLLING_WINDOW = timedelta(seconds=30)


@dataclass(slots=True, frozen=True)
class WorkItem:
    """One session enqueued by a control plane and leased to a worker.

    ``metadata`` carries session-staging references (file paths, commit SHAs)
    the control plane attached; the lab never mounts files into a self-hosted
    environment, so handlers stage inputs from these references themselves.
    """

    id: str
    session_id: str
    environment_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    enqueued_at: datetime | None = None
    attempt: int = 1


@dataclass(slots=True, frozen=True)
class WorkResult:
    """Terminal outcome a worker posts back for one work item."""

    status: WorkResultStatus
    detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WorkQueueStats:
    """Queue state for ops tooling, field-for-field the platform stats shape.

    ``depth`` items wait to be claimed; ``pending`` items are claimed and in
    flight; ``workers_polling`` counts workers seen inside
    :data:`WORKERS_POLLING_WINDOW` and is the liveness signal to alert on.
    """

    depth: int = 0
    pending: int = 0
    oldest_queued_at: datetime | None = None
    workers_polling: int = 0


@dataclass(slots=True, frozen=True)
class WorkerCredentials:
    """Scoped credential pair that authenticates a worker to its queue.

    The environment key is a low-privilege, queue-scoped credential; the
    organization/provider API key stays on ops hosts and must never reach the
    worker host, where agent tool calls could read it. The key is excluded
    from ``repr`` so it cannot leak through logs or error text.
    """

    environment_id: str
    environment_key: str = field(repr=False)


@runtime_checkable
class WorkSource(Protocol):
    """Asynchronous claim/lease work queue published by a control plane."""

    async def claim(
        self,
        *,
        block_ms: int | None = None,
        reclaim_older_than_ms: int | None = None,
    ) -> WorkItem | None: ...

    async def heartbeat(self, work_id: str) -> None: ...

    async def complete(self, work_id: str, result: WorkResult) -> None: ...

    async def stop_requested(self, work_id: str) -> bool: ...

    async def stats(self) -> WorkQueueStats: ...


# --- reference implementation -------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _WorkState:
    item: WorkItem
    status: WorkItemStatus = "queued"
    claimed_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    stop_requested: bool = False
    stop_forced: bool = False
    result: WorkResult | None = None


@dataclass(slots=True)
class InMemoryWorkSource:
    """In-process reference :class:`WorkSource`.

    Single-process by design: it exercises the full claim/lease/reclaim/stop
    contract for tests and local development, and is the seam where a
    customer-owned control plane would substitute a durable queue. ``clock``
    is injectable so lease expiry is deterministic in tests. ``block_ms`` is
    accepted but ignored (claims return immediately); ``workers_polling``
    reports at most 1 because claims carry no worker identity here.
    """

    clock: Callable[[], datetime] = _utc_now
    _states: dict[str, _WorkState] = field(default_factory=dict)
    _last_claim_poll_at: datetime | None = None

    # -- control-plane / ops side --

    def enqueue(
        self,
        session_id: str,
        *,
        environment_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkItem:
        """Enqueue a session as a work item (the control-plane side)."""

        item = WorkItem(
            id=f"work_{uuid4().hex}",
            session_id=session_id,
            environment_id=environment_id,
            metadata=dict(metadata or {}),
            enqueued_at=self.clock(),
        )
        self._states[item.id] = _WorkState(item=item)
        return item

    def request_stop(self, work_id: str, *, force: bool = False) -> None:
        """Ask the worker handling ``work_id`` to stop it.

        A queued item is marked ``stopped`` immediately; a claimed item gets
        the stop flag, which the worker observes through
        :meth:`stop_requested` and answers by cancelling its handler.
        """

        state = self._require(work_id)
        if state.status == "queued":
            state.status = "stopped"
            state.result = WorkResult(status="stopped", detail="stopped before claim")
            return
        state.stop_requested = True
        state.stop_forced = force

    def result_for(self, work_id: str) -> WorkResult | None:
        """Posted result for a work item, or ``None`` while it is open."""

        return self._require(work_id).result

    def status_for(self, work_id: str) -> WorkItemStatus:
        return self._require(work_id).status

    # -- worker side (WorkSource protocol) --

    async def claim(
        self,
        *,
        block_ms: int | None = None,
        reclaim_older_than_ms: int | None = None,
    ) -> WorkItem | None:
        now = self.clock()
        self._last_claim_poll_at = now
        for state in self._states.values():
            if state.status == "queued":
                state.status = "claimed"
                state.claimed_at = now
                state.last_heartbeat_at = now
                return state.item
        if reclaim_older_than_ms is not None:
            horizon = now - timedelta(milliseconds=reclaim_older_than_ms)
            for state in self._states.values():
                lease_seen = state.last_heartbeat_at or state.claimed_at
                if state.status == "claimed" and lease_seen is not None and lease_seen < horizon:
                    reclaimed = WorkItem(
                        id=state.item.id,
                        session_id=state.item.session_id,
                        environment_id=state.item.environment_id,
                        metadata=state.item.metadata,
                        enqueued_at=state.item.enqueued_at,
                        attempt=state.item.attempt + 1,
                    )
                    state.item = reclaimed
                    state.claimed_at = now
                    state.last_heartbeat_at = now
                    state.stop_requested = False
                    state.stop_forced = False
                    return reclaimed
        return None

    async def heartbeat(self, work_id: str) -> None:
        state = self._require(work_id)
        if state.status != "claimed":
            raise WorkSourceError(
                f"Work item {work_id!r} is {state.status}, not claimed; lease is gone."
            )
        state.last_heartbeat_at = self.clock()

    async def complete(self, work_id: str, result: WorkResult) -> None:
        state = self._require(work_id)
        if state.status != "claimed":
            raise WorkSourceError(
                f"Work item {work_id!r} is {state.status}; only claimed items can complete."
            )
        state.status = result.status
        state.result = result

    async def stop_requested(self, work_id: str) -> bool:
        return self._require(work_id).stop_requested

    async def stats(self) -> WorkQueueStats:
        now = self.clock()
        queued = [s for s in self._states.values() if s.status == "queued"]
        pending = sum(1 for s in self._states.values() if s.status == "claimed")
        oldest = min(
            (s.item.enqueued_at for s in queued if s.item.enqueued_at is not None),
            default=None,
        )
        polling = (
            1
            if self._last_claim_poll_at is not None
            and now - self._last_claim_poll_at <= WORKERS_POLLING_WINDOW
            else 0
        )
        return WorkQueueStats(
            depth=len(queued),
            pending=pending,
            oldest_queued_at=oldest,
            workers_polling=polling,
        )

    def _require(self, work_id: str) -> _WorkState:
        try:
            return self._states[work_id]
        except KeyError as exc:
            raise WorkItemNotFoundError(f"Unknown work item: {work_id!r}") from exc
