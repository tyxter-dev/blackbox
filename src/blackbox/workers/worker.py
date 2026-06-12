"""Reference environment worker: claims work items and runs them under policy.

The worker is the customer-side daemon of the connector: it claims work from
a :class:`~blackbox.workers.source.WorkSource`, gates each claimed item
through the ``before_work_claim`` policy checkpoint, executes the session via
an injected :data:`WorkHandler`, keeps the lease alive while the handler
runs, and posts a terminal :class:`~blackbox.workers.source.WorkResult`.

Two deployment shapes, mirroring the platform patterns:

- **Always-on**: :meth:`EnvironmentWorker.run` polls continuously and exits
  cleanly when :meth:`EnvironmentWorker.stop` is called — in-flight work
  drains first (wire ``stop`` to SIGTERM in your entrypoint).
- **Webhook-triggered**: a webhook handler wakes on the lab's
  session-started event and calls :meth:`EnvironmentWorker.drain`, which
  claims until the queue is empty and returns.

Governance: a ``deny`` or ``require_approval`` verdict at ``before_work_claim``
completes the item as ``skipped`` without executing it (there is no approval
channel at the worker boundary yet, so ``require_approval`` is conservative).
Per-tool-call gating belongs inside the handler — handlers built on the
blackbox ``ToolRuntime`` get ``before_tool_call`` / ``before_command``
checkpoints for free.

Stop semantics: a control-plane stop (``stop_requested`` turning true)
cancels the in-flight handler and posts ``stopped``. A lost lease (heartbeat
rejected because the item was reclaimed) cancels the handler and posts
nothing — the reclaiming worker owns the item now; the loss is visible in
:class:`WorkerStatus`.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from blackbox.core.errors import WorkSourceError
from blackbox.core.policy import Policy, PolicyRequest
from blackbox.workers.source import WorkItem, WorkResult, WorkSource

#: Executes one claimed session inside the customer boundary. Source-specific:
#: the Anthropic adapter ships ``anthropic_sdk_session_handler``; handlers for
#: a custom control plane typically wrap the blackbox runtime or ToolRuntime.
WorkHandler = Callable[[WorkItem], Awaitable[WorkResult]]

WorkerState = Literal["idle", "handling", "stopped"]


@dataclass(slots=True, frozen=True)
class WorkerStatus:
    """Point-in-time liveness snapshot for ops tooling."""

    state: WorkerState
    last_polled_at: datetime | None = None
    in_flight_work_id: str | None = None
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    stopped: int = 0
    lost: int = 0

    @property
    def handled(self) -> int:
        return self.completed + self.failed + self.skipped + self.stopped + self.lost


@dataclass(slots=True)
class EnvironmentWorker:
    """Claims work items from a source and executes them through a handler."""

    source: WorkSource
    handler: WorkHandler
    policy: Policy | None = None
    heartbeat_seconds: float = 15.0
    _state: WorkerState = "idle"
    _last_polled_at: datetime | None = None
    _in_flight_work_id: str | None = None
    _counts: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            ("completed", "failed", "skipped", "stopped", "lost"), 0
        )
    )
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event)

    def status(self) -> WorkerStatus:
        return WorkerStatus(
            state=self._state,
            last_polled_at=self._last_polled_at,
            in_flight_work_id=self._in_flight_work_id,
            completed=self._counts["completed"],
            failed=self._counts["failed"],
            skipped=self._counts["skipped"],
            stopped=self._counts["stopped"],
            lost=self._counts["lost"],
        )

    def stop(self) -> None:
        """Request a graceful exit: in-flight work finishes, then loops return."""

        self._stop_event.set()

    async def run(
        self,
        *,
        block_ms: int | None = None,
        reclaim_older_than_ms: int | None = None,
        idle_seconds: float = 1.0,
    ) -> None:
        """Always-on loop: poll, handle, repeat until :meth:`stop` is called."""

        self._stop_event.clear()
        self._state = "idle"
        while not self._stop_event.is_set():
            handled = await self.handle_one(
                block_ms=block_ms, reclaim_older_than_ms=reclaim_older_than_ms
            )
            if handled is not None or self._stop_event.is_set():
                continue
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=idle_seconds)
            except TimeoutError:
                continue
        self._state = "stopped"

    async def drain(
        self,
        *,
        block_ms: int | None = None,
        reclaim_older_than_ms: int | None = None,
    ) -> list[WorkResult]:
        """Webhook-triggered shape: handle items until the queue is empty."""

        results: list[WorkResult] = []
        while not self._stop_event.is_set():
            result = await self.handle_one(
                block_ms=block_ms, reclaim_older_than_ms=reclaim_older_than_ms
            )
            if result is None:
                break
            results.append(result)
        return results

    async def handle_one(
        self,
        *,
        block_ms: int | None = None,
        reclaim_older_than_ms: int | None = None,
    ) -> WorkResult | None:
        """Claim and handle a single item; ``None`` when the queue is empty.

        Returns the posted result, or a synthetic ``stopped`` result with
        ``metadata["lease_lost"]`` set when the lease was reclaimed mid-flight
        (in that case nothing was posted — the item belongs to another worker).
        """

        work = await self.source.claim(
            block_ms=block_ms, reclaim_older_than_ms=reclaim_older_than_ms
        )
        self._last_polled_at = datetime.now(UTC)
        if work is None:
            return None
        return await self._process(work)

    async def _process(self, work: WorkItem) -> WorkResult:
        self._state = "handling"
        self._in_flight_work_id = work.id
        try:
            decision_result = await self._check_policy(work)
            if decision_result is not None:
                await self.source.complete(work.id, decision_result)
                self._counts["skipped"] += 1
                return decision_result
            result, lease_lost = await self._run_handler(work)
            if lease_lost:
                self._counts["lost"] += 1
                return result
            await self.source.complete(work.id, result)
            self._counts[result.status] += 1
            return result
        finally:
            self._state = "stopped" if self._stop_event.is_set() else "idle"
            self._in_flight_work_id = None

    async def _check_policy(self, work: WorkItem) -> WorkResult | None:
        if self.policy is None:
            return None
        decision = await self.policy.check(
            PolicyRequest(
                checkpoint="before_work_claim",
                action=work.session_id,
                arguments={
                    "work_id": work.id,
                    "environment_id": work.environment_id,
                    "metadata": work.metadata,
                },
            )
        )
        if decision.verdict == "allow":
            return None
        return WorkResult(
            status="skipped",
            detail=decision.reason or decision.verdict,
            metadata={"policy_verdict": decision.verdict},
        )

    async def _run_handler(self, work: WorkItem) -> tuple[WorkResult, bool]:
        """Run the handler with lease keep-alive; returns (result, lease_lost)."""

        async def _invoke() -> WorkResult:
            return await self.handler(work)

        handler_task: asyncio.Task[WorkResult] = asyncio.create_task(_invoke())
        try:
            while True:
                done, _ = await asyncio.wait({handler_task}, timeout=self.heartbeat_seconds)
                if done:
                    return self._handler_outcome(handler_task), False
                try:
                    await self.source.heartbeat(work.id)
                    interrupt = await self.source.stop_requested(work.id)
                except WorkSourceError as exc:
                    await self._cancel(handler_task)
                    return (
                        WorkResult(
                            status="stopped",
                            detail=str(exc),
                            metadata={"lease_lost": True},
                        ),
                        True,
                    )
                if interrupt:
                    await self._cancel(handler_task)
                    return (
                        WorkResult(status="stopped", detail="stop requested by control plane"),
                        False,
                    )
        finally:
            if not handler_task.done():
                await self._cancel(handler_task)

    @staticmethod
    def _handler_outcome(handler_task: asyncio.Task[WorkResult]) -> WorkResult:
        try:
            return handler_task.result()
        except asyncio.CancelledError:
            return WorkResult(status="stopped", detail="handler cancelled")
        except Exception as exc:
            return WorkResult(
                status="failed",
                detail=str(exc),
                metadata={"error": str(exc), "error_type": type(exc).__name__},
            )

    @staticmethod
    async def _cancel(handler_task: asyncio.Task[Any]) -> None:
        handler_task.cancel()
        try:
            await handler_task
        except (asyncio.CancelledError, Exception):
            pass
