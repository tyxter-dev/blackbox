"""Anthropic Managed Agents adapter for the :class:`WorkSource` contract.

Maps the lab-neutral claim/lease vocabulary onto Anthropic's environments
work API (``client.beta.environments.work``, beta header
``managed-agents-2026-04-01``). The client is injected, never imported here,
so this module is import-safe without the ``anthropic`` extra installed.

.. warning::
   This adapter tracks a **beta** API as documented on 2026-06-12 (see
   ``docs/ENVIRONMENT_WORKERS.md`` for the snapshot analysis). Re-verify the
   SDK helper shapes against the live docs before depending on it; every SDK
   touchpoint feature-detects and raises ``ProviderNotConfiguredError`` with
   a pointer back here when the surface is missing or has drifted.

Contract mapping:

- ``claim`` takes one item from a fresh SDK work poller opened with
  ``drain=True`` (stop when empty) and ``auto_stop=False`` (this adapter owns
  the stop call via ``complete``).
- ``heartbeat`` is a no-op: the platform manages the claim lease inside its
  helpers, and dead workers are covered by ``reclaim_older_than_ms``.
- ``complete`` posts the platform stop signal for the work item; interrupting
  results (``stopped``/``failed``) pass ``force=True``.
- ``stop_requested`` always reports ``False``: the documented API exposes no
  worker-side read for a pending stop; the platform delivers stops through
  its own session channel instead.

The worker host should hold only the environment key
(:class:`~blackbox.workers.source.WorkerCredentials`), never the organization
API key.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from blackbox.core.errors import ProviderExecutionError, ProviderNotConfiguredError
from blackbox.workers.source import WorkerCredentials, WorkItem, WorkQueueStats, WorkResult
from blackbox.workers.worker import WorkHandler

_REVERIFY = (
    "the Anthropic environments work API (beta managed-agents-2026-04-01) was not "
    "found on the client. Install an anthropic SDK release with Managed Agents "
    "support and re-verify the helper shapes against the live docs "
    "(docs/ENVIRONMENT_WORKERS.md)."
)


def _work_api(client: Any) -> Any:
    beta = getattr(client, "beta", None)
    environments = getattr(beta, "environments", None)
    work = getattr(environments, "work", None)
    if work is None:
        raise ProviderNotConfiguredError(f"AnthropicEnvironmentWorkSource: {_REVERIFY}")
    return work


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class AnthropicEnvironmentWorkSource:
    """:class:`~blackbox.workers.source.WorkSource` over an Anthropic client.

    ``client`` is an ``anthropic.AsyncAnthropic`` instance authenticated with
    the environment key (``AsyncAnthropic(auth_token=credentials.environment_key)``).
    """

    def __init__(self, client: Any, credentials: WorkerCredentials) -> None:
        self._client = client
        self._credentials = credentials

    async def claim(
        self,
        *,
        block_ms: int | None = None,
        reclaim_older_than_ms: int | None = None,
    ) -> WorkItem | None:
        work_api = _work_api(self._client)
        poller_kwargs: dict[str, Any] = {
            "environment_id": self._credentials.environment_id,
            "environment_key": self._credentials.environment_key,
            "block_ms": block_ms,
            "drain": True,
            "auto_stop": False,
        }
        if reclaim_older_than_ms is not None:
            poller_kwargs["reclaim_older_than_ms"] = reclaim_older_than_ms
        try:
            poller = work_api.poller(**poller_kwargs)
        except TypeError as exc:
            raise ProviderNotConfiguredError(
                f"AnthropicEnvironmentWorkSource: work poller signature drifted; {_REVERIFY}"
            ) from exc
        try:
            async for work in poller:
                return self._to_work_item(work)
            return None
        except (ProviderExecutionError, ProviderNotConfiguredError):
            raise
        except Exception as exc:
            raise ProviderExecutionError(f"Anthropic work claim failed: {exc}") from exc
        finally:
            aclose = getattr(poller, "aclose", None)
            if aclose is not None:
                await aclose()

    async def heartbeat(self, work_id: str) -> None:
        """No-op: the platform lease is managed inside the SDK helpers."""

    async def complete(self, work_id: str, result: WorkResult) -> None:
        """Post the platform stop signal for the work item.

        Best-effort for ``completed`` results: when execution was delegated to
        the SDK session worker, ``handle_item`` may have already released the
        item, making this a duplicate stop the platform can reject — that
        rejection is swallowed. Failures on interrupting results raise.
        """

        work_api = _work_api(self._client)
        kwargs: dict[str, Any] = {"environment_id": self._credentials.environment_id}
        if result.status in ("stopped", "failed"):
            kwargs["force"] = True
        try:
            await work_api.stop(work_id, **kwargs)
        except TypeError:
            # Helper without a force parameter: fall back to a plain stop.
            await work_api.stop(work_id, environment_id=self._credentials.environment_id)
        except Exception as exc:
            if result.status == "completed":
                return
            raise ProviderExecutionError(f"Anthropic work stop failed: {exc}") from exc

    async def stop_requested(self, work_id: str) -> bool:
        """Always ``False``: no worker-side read for pending stops is documented."""

        return False

    async def stats(self) -> WorkQueueStats:
        work_api = _work_api(self._client)
        try:
            stats = await work_api.stats(self._credentials.environment_id)
        except Exception as exc:
            raise ProviderExecutionError(f"Anthropic work stats failed: {exc}") from exc
        return WorkQueueStats(
            depth=int(getattr(stats, "depth", 0) or 0),
            pending=int(getattr(stats, "pending", 0) or 0),
            oldest_queued_at=_parse_timestamp(getattr(stats, "oldest_queued_at", None)),
            workers_polling=int(getattr(stats, "workers_polling", 0) or 0),
        )

    def _to_work_item(self, work: Any) -> WorkItem:
        data = getattr(work, "data", None)
        session_id = getattr(data, "id", None)
        if session_id is None:
            raise ProviderExecutionError(
                "Anthropic work item carried no session id (work.data.id); "
                f"{_REVERIFY}"
            )
        raw_metadata = getattr(data, "metadata", None)
        metadata: dict[str, Any] = dict(raw_metadata) if raw_metadata else {}
        return WorkItem(
            id=str(work.id),
            session_id=str(session_id),
            environment_id=self._credentials.environment_id,
            metadata=metadata,
            enqueued_at=_parse_timestamp(getattr(work, "created_at", None)),
        )


def anthropic_sdk_session_handler(
    client: Any,
    credentials: WorkerCredentials,
    *,
    workdir: str = "/workspace",
    tools: Any | None = None,
) -> WorkHandler:
    """Build a :data:`WorkHandler` that delegates execution to the SDK worker.

    The returned handler runs one claimed session end to end through
    ``work.worker(workdir=...).handle_item(...)``: the SDK downloads the
    agent's skills into ``<workdir>/skills/<name>/``, executes the standard
    toolset (``bash``/``read``/``write``/``edit``/``glob``/``grep``), and
    posts tool results to the control plane. Pass ``tools`` (the SDK's
    ``tools`` factory) to substitute a customized — e.g. policy-gated — tool
    list. Requires a Linux host with ``/bin/bash``.
    """

    work_api = _work_api(client)
    worker_factory = getattr(work_api, "worker", None)
    if worker_factory is None:
        raise ProviderNotConfiguredError(f"anthropic_sdk_session_handler: {_REVERIFY}")
    worker_kwargs: dict[str, Any] = {"workdir": workdir}
    if tools is not None:
        worker_kwargs["tools"] = tools
    sdk_worker = worker_factory(**worker_kwargs)

    async def handle(work: WorkItem) -> WorkResult:
        await sdk_worker.handle_item(
            work_id=work.id,
            environment_id=credentials.environment_id,
            session_id=work.session_id,
            environment_key=credentials.environment_key,
        )
        return WorkResult(status="completed", metadata={"workdir": workdir})

    return handle
