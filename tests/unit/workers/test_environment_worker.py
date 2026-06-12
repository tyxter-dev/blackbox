"""Environment worker: drain/run loops, policy gating, stop and lease handling."""
from __future__ import annotations

import asyncio

from blackbox.core.policy import PolicyDecision, PolicyRequest
from blackbox.workers import EnvironmentWorker, InMemoryWorkSource, WorkItem, WorkResult


async def echo_handler(work: WorkItem) -> WorkResult:
    return WorkResult(status="completed", detail=work.session_id)


async def test_drain_handles_all_items_and_posts_results() -> None:
    source = InMemoryWorkSource()
    first = source.enqueue("sess_a")
    second = source.enqueue("sess_b")
    worker = EnvironmentWorker(source=source, handler=echo_handler)

    results = await worker.drain()

    assert [r.detail for r in results] == ["sess_a", "sess_b"]
    assert source.status_for(first.id) == "completed"
    assert source.status_for(second.id) == "completed"
    status = worker.status()
    assert status.completed == 2
    assert status.handled == 2
    assert status.state == "idle"
    assert status.last_polled_at is not None


async def test_handle_one_returns_none_on_empty_queue() -> None:
    worker = EnvironmentWorker(source=InMemoryWorkSource(), handler=echo_handler)
    assert await worker.handle_one() is None


async def test_policy_deny_skips_without_executing() -> None:
    calls: list[str] = []

    async def recording_handler(work: WorkItem) -> WorkResult:
        calls.append(work.session_id)
        return WorkResult(status="completed")

    class DenySecond:
        async def check(self, request: PolicyRequest) -> PolicyDecision:
            assert request.checkpoint == "before_work_claim"
            assert "work_id" in request.arguments
            if request.action == "sess_denied":
                return PolicyDecision.deny("tenant not allowed")
            return PolicyDecision.allow()

    source = InMemoryWorkSource()
    source.enqueue("sess_ok")
    denied = source.enqueue("sess_denied")
    worker = EnvironmentWorker(source=source, handler=recording_handler, policy=DenySecond())

    results = await worker.drain()

    assert calls == ["sess_ok"]
    assert [r.status for r in results] == ["completed", "skipped"]
    assert results[1].detail == "tenant not allowed"
    assert source.status_for(denied.id) == "skipped"
    assert worker.status().skipped == 1


async def test_require_approval_is_treated_as_skip() -> None:
    class RequireApproval:
        async def check(self, request: PolicyRequest) -> PolicyDecision:
            return PolicyDecision.require_approval("needs human sign-off")

    source = InMemoryWorkSource()
    item = source.enqueue("sess_1")
    worker = EnvironmentWorker(source=source, handler=echo_handler, policy=RequireApproval())

    result = await worker.handle_one()

    assert result is not None and result.status == "skipped"
    assert result.metadata["policy_verdict"] == "require_approval"
    assert source.status_for(item.id) == "skipped"


async def test_handler_exception_posts_failed_result() -> None:
    async def boom(work: WorkItem) -> WorkResult:
        raise RuntimeError("session exploded")

    source = InMemoryWorkSource()
    item = source.enqueue("sess_1")
    worker = EnvironmentWorker(source=source, handler=boom)

    result = await worker.handle_one()

    assert result is not None and result.status == "failed"
    assert result.metadata["error_type"] == "RuntimeError"
    assert source.status_for(item.id) == "failed"
    assert worker.status().failed == 1


async def test_control_plane_stop_cancels_in_flight_handler() -> None:
    started = asyncio.Event()

    async def hang(work: WorkItem) -> WorkResult:
        started.set()
        await asyncio.Event().wait()  # never returns on its own
        return WorkResult(status="completed")

    source = InMemoryWorkSource()
    item = source.enqueue("sess_1")
    worker = EnvironmentWorker(source=source, handler=hang, heartbeat_seconds=0.01)

    handle_task = asyncio.create_task(worker.handle_one())
    await started.wait()
    source.request_stop(item.id, force=True)
    result = await asyncio.wait_for(handle_task, timeout=2)

    assert result is not None and result.status == "stopped"
    assert source.status_for(item.id) == "stopped"
    assert worker.status().stopped == 1


async def test_lost_lease_cancels_handler_without_posting() -> None:
    started = asyncio.Event()

    async def hang(work: WorkItem) -> WorkResult:
        started.set()
        await asyncio.Event().wait()
        return WorkResult(status="completed")

    source = InMemoryWorkSource()
    item = source.enqueue("sess_1")
    worker = EnvironmentWorker(source=source, handler=hang, heartbeat_seconds=0.01)

    handle_task = asyncio.create_task(worker.handle_one())
    await started.wait()
    # Simulate another worker reclaiming the item: complete it out from under us.
    await source.complete(item.id, WorkResult(status="completed", detail="other worker"))
    result = await asyncio.wait_for(handle_task, timeout=2)

    assert result is not None and result.status == "stopped"
    assert result.metadata.get("lease_lost") is True
    assert worker.status().lost == 1
    # The other worker's result is untouched.
    posted = source.result_for(item.id)
    assert posted is not None and posted.detail == "other worker"


async def test_heartbeat_keeps_lease_alive_during_long_handler() -> None:
    release = asyncio.Event()

    async def slow(work: WorkItem) -> WorkResult:
        await release.wait()
        return WorkResult(status="completed")

    source = InMemoryWorkSource()
    item = source.enqueue("sess_1")
    worker = EnvironmentWorker(source=source, handler=slow, heartbeat_seconds=0.01)

    handle_task = asyncio.create_task(worker.handle_one())
    await asyncio.sleep(0.05)  # let several heartbeats land
    state = source._states[item.id]
    assert state.last_heartbeat_at is not None
    assert state.claimed_at is not None
    assert state.last_heartbeat_at >= state.claimed_at
    release.set()
    result = await asyncio.wait_for(handle_task, timeout=2)
    assert result is not None and result.status == "completed"


async def test_run_loop_handles_then_exits_on_stop() -> None:
    source = InMemoryWorkSource()
    source.enqueue("sess_a")
    source.enqueue("sess_b")
    worker = EnvironmentWorker(source=source, handler=echo_handler)

    async def stopping_handler(work: WorkItem) -> WorkResult:
        if work.session_id == "sess_b":
            worker.stop()
        return await echo_handler(work)

    worker.handler = stopping_handler
    await asyncio.wait_for(worker.run(idle_seconds=0.01), timeout=2)

    status = worker.status()
    assert status.completed == 2
    assert status.state == "stopped"
