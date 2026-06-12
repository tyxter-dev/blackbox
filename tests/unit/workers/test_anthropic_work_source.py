"""Anthropic work-source adapter against a fake client (no SDK required)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from blackbox.core.errors import ProviderExecutionError, ProviderNotConfiguredError
from blackbox.workers import (
    AnthropicEnvironmentWorkSource,
    WorkerCredentials,
    WorkItem,
    WorkResult,
    WorkSource,
    anthropic_sdk_session_handler,
)

CREDS = WorkerCredentials(environment_id="env_1", environment_key="sk-ant-oat01-test")


class FakePoller:
    """Async-iterable shaped like the SDK work poller."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)
        self.closed = False

    def __aiter__(self) -> FakePoller:
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class FakeWorkAPI:
    items: list[Any] = field(default_factory=list)
    poller_kwargs: dict[str, Any] = field(default_factory=dict)
    stop_calls: list[dict[str, Any]] = field(default_factory=list)
    stats_payload: Any = None
    last_poller: FakePoller | None = None

    def poller(self, **kwargs: Any) -> FakePoller:
        self.poller_kwargs = kwargs
        self.last_poller = FakePoller(self.items)
        return self.last_poller

    async def stop(self, work_id: str, **kwargs: Any) -> None:
        self.stop_calls.append({"work_id": work_id, **kwargs})

    async def stats(self, environment_id: str) -> Any:
        return self.stats_payload

    def worker(self, **kwargs: Any) -> Any:
        return FakeSDKWorker(**kwargs)


class FakeSDKWorker:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.handled: list[dict[str, Any]] = []

    async def handle_item(self, **kwargs: Any) -> None:
        self.handled.append(kwargs)


def make_client(work_api: FakeWorkAPI | None = None) -> Any:
    api = work_api if work_api is not None else FakeWorkAPI()
    return SimpleNamespace(beta=SimpleNamespace(environments=SimpleNamespace(work=api))), api


def fake_work(work_id: str = "work_1", session_id: str = "sess_1") -> Any:
    return SimpleNamespace(
        id=work_id,
        created_at="2026-06-12T15:00:00+00:00",
        data=SimpleNamespace(id=session_id, metadata={"input_file": "s3://bucket/data.csv"}),
    )


def test_satisfies_work_source_protocol() -> None:
    client, _ = make_client()
    assert isinstance(AnthropicEnvironmentWorkSource(client, CREDS), WorkSource)


async def test_claim_maps_work_item_and_closes_poller() -> None:
    client, api = make_client(FakeWorkAPI(items=[fake_work()]))
    source = AnthropicEnvironmentWorkSource(client, CREDS)

    item = await source.claim(block_ms=None, reclaim_older_than_ms=2000)

    assert item is not None
    assert item.id == "work_1"
    assert item.session_id == "sess_1"
    assert item.environment_id == "env_1"
    assert item.metadata == {"input_file": "s3://bucket/data.csv"}
    assert item.enqueued_at == datetime(2026, 6, 12, 15, 0, tzinfo=UTC)
    assert api.poller_kwargs["environment_id"] == "env_1"
    assert api.poller_kwargs["environment_key"] == "sk-ant-oat01-test"
    assert api.poller_kwargs["drain"] is True
    assert api.poller_kwargs["auto_stop"] is False
    assert api.poller_kwargs["reclaim_older_than_ms"] == 2000
    assert api.last_poller is not None and api.last_poller.closed


async def test_claim_empty_queue_returns_none() -> None:
    client, _ = make_client()
    source = AnthropicEnvironmentWorkSource(client, CREDS)
    assert await source.claim() is None


async def test_missing_beta_surface_raises_provider_not_configured() -> None:
    bare_client = SimpleNamespace(beta=SimpleNamespace())
    source = AnthropicEnvironmentWorkSource(bare_client, CREDS)
    with pytest.raises(ProviderNotConfiguredError, match="re-verify"):
        await source.claim()
    with pytest.raises(ProviderNotConfiguredError):
        await source.stats()


async def test_claim_without_session_id_raises_execution_error() -> None:
    malformed = SimpleNamespace(id="work_1", data=SimpleNamespace(id=None, metadata=None))
    client, _ = make_client(FakeWorkAPI(items=[malformed]))
    source = AnthropicEnvironmentWorkSource(client, CREDS)
    with pytest.raises(ProviderExecutionError, match="session id"):
        await source.claim()


async def test_complete_posts_stop_with_force_for_interrupting_results() -> None:
    client, api = make_client()
    source = AnthropicEnvironmentWorkSource(client, CREDS)

    await source.complete("work_1", WorkResult(status="completed"))
    await source.complete("work_2", WorkResult(status="stopped"))

    assert api.stop_calls[0] == {"work_id": "work_1", "environment_id": "env_1"}
    assert api.stop_calls[1] == {"work_id": "work_2", "environment_id": "env_1", "force": True}


async def test_complete_swallows_duplicate_stop_for_completed_results() -> None:
    class RejectingAPI(FakeWorkAPI):
        async def stop(self, work_id: str, **kwargs: Any) -> None:
            raise RuntimeError("work item already released")

    client, _ = make_client(RejectingAPI())
    source = AnthropicEnvironmentWorkSource(client, CREDS)

    await source.complete("work_1", WorkResult(status="completed"))  # no raise
    with pytest.raises(ProviderExecutionError):
        await source.complete("work_2", WorkResult(status="stopped"))


async def test_stats_maps_documented_fields() -> None:
    payload = SimpleNamespace(
        depth=3, pending=1, oldest_queued_at="2026-06-12T14:00:00+00:00", workers_polling=2
    )
    client, _ = make_client(FakeWorkAPI(stats_payload=payload))
    source = AnthropicEnvironmentWorkSource(client, CREDS)

    stats = await source.stats()

    assert stats.depth == 3
    assert stats.pending == 1
    assert stats.workers_polling == 2
    assert stats.oldest_queued_at == datetime(2026, 6, 12, 14, 0, tzinfo=UTC)


async def test_heartbeat_is_noop_and_stop_requested_false() -> None:
    client, _ = make_client()
    source = AnthropicEnvironmentWorkSource(client, CREDS)
    await source.heartbeat("work_1")
    assert await source.stop_requested("work_1") is False


async def test_sdk_session_handler_delegates_to_handle_item() -> None:
    captured: list[FakeSDKWorker] = []

    class CapturingAPI(FakeWorkAPI):
        def worker(self, **kwargs: Any) -> Any:
            sdk_worker = FakeSDKWorker(**kwargs)
            captured.append(sdk_worker)
            return sdk_worker

    client, _ = make_client(CapturingAPI())
    handler = anthropic_sdk_session_handler(client, CREDS, workdir="/srv/work")

    result = await handler(WorkItem(id="work_9", session_id="sess_9"))

    assert result.status == "completed"
    assert captured[0].kwargs == {"workdir": "/srv/work"}
    assert captured[0].handled == [
        {
            "work_id": "work_9",
            "environment_id": "env_1",
            "session_id": "sess_9",
            "environment_key": "sk-ant-oat01-test",
        }
    ]


async def test_sdk_session_handler_requires_worker_helper() -> None:
    class NoWorkerAPI(FakeWorkAPI):
        worker = None  # type: ignore[assignment]

    client, _ = make_client(NoWorkerAPI())
    with pytest.raises(ProviderNotConfiguredError):
        anthropic_sdk_session_handler(client, CREDS)
