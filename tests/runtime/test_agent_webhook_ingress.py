from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from blackbox import (
    AgentRuntime,
    AgentSpec,
    AgentWebhookDelivery,
    AgentWebhookIngestResult,
    EventTypes,
)
from blackbox.core.artifacts import ArtifactPage
from blackbox.core.capabilities import AgentCapabilities
from blackbox.core.errors import UnsupportedFeatureError
from blackbox.core.events import AgentEvent
from blackbox.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from blackbox.core.stores import SQLiteSessionStore
from blackbox.providers.agent_adapters.local import LocalAgentProvider
from blackbox.providers.base import TaskSpec


class WebhookCapableAgentProvider:
    provider_id = "webhook-agent"

    def __init__(self) -> None:
        self.deliveries: list[AgentWebhookDelivery] = []

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            supports_sessions=True,
            supports_streaming_events=True,
            supports_webhooks=True,
            supports_resume=True,
        )

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        return AgentRef(provider=self.provider_id, id=spec.name)

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        agent_id = agent.id if isinstance(agent, AgentRef) else agent
        return AgentSession(
            provider=self.provider_id,
            agent_id=agent_id,
            task=task.prompt,
            status="running",
            id="sess_webhook_1",
            metadata={"provider_session_id": "provider_sess_1"},
        )

    async def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if False:
            yield AgentEvent(type=EventTypes.SESSION_STARTED, session_id=session.id)

    async def send_message(self, session: SessionRef | AgentSession, message: str) -> InvocationRef:
        return InvocationRef(provider=self.provider_id, session_id=session.id, id="inv_webhook")

    async def approve(self, approval_id: str, decision: object) -> None:
        return None

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        return None

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        return ArtifactPage(items=[])

    async def ingest_webhook(
        self,
        delivery: AgentWebhookDelivery,
    ) -> AgentWebhookIngestResult:
        self.deliveries.append(delivery)
        return AgentWebhookIngestResult(
            provider=self.provider_id,
            status="reconcile_required",
            webhook_event_id="wh_evt_1",
            webhook_event_type="session.status_updated",
            session_ref=SessionRef(
                provider=self.provider_id,
                id="sess_webhook_1",
                agent_id="repo-agent",
                metadata={"provider_session_id": "provider_sess_1"},
            ),
            provider_session_id="provider_sess_1",
            dedupe_key="webhook-agent:wh_evt_1",
            requires_session_fetch=True,
            events=[
                AgentEvent(
                    id="evt_provider_status_1",
                    type=EventTypes.CLOUD_AGENT_STATUS_CHANGED,
                    data={
                        "status": "completed",
                        "provider_event_id": "provider_evt_1",
                        "provider_cursor": "cursor_1",
                    },
                    raw={"id": "provider_evt_1"},
                )
            ],
        )


async def test_agent_webhook_ingress_requires_opt_in_provider() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_agent(LocalAgentProvider(runtime.models))

    with pytest.raises(UnsupportedFeatureError, match="webhook ingress"):
        await runtime.agents.ingest_webhook(
            provider="local",
            headers={},
            body=b"{}",
        )


async def test_agent_webhook_ingress_persists_normalized_events(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    runtime = AgentRuntime(session_store=store)
    provider = WebhookCapableAgentProvider()
    runtime.registry.register_agent(provider)

    session = await runtime.agents.create_session(
        provider="webhook-agent",
        agent="repo-agent",
        task="Fix tests",
    )

    result = await runtime.agents.ingest_webhook(
        provider="webhook-agent",
        headers={"x-provider-signature": "sig"},
        body=b'{"type":"session.status_updated"}',
        metadata={"request_id": "req_1"},
    )
    replayed = await runtime.agents.replay(session)
    state = await store.load_session_state(session.id)

    assert result.status == "reconcile_required"
    assert result.requires_session_fetch is True
    assert provider.deliveries[0].provider == "webhook-agent"
    assert provider.deliveries[0].headers["x-provider-signature"] == "sig"
    assert provider.deliveries[0].metadata["request_id"] == "req_1"
    assert [event.id for event in replayed] == ["evt_provider_status_1"]
    assert replayed[0].provider == "webhook-agent"
    assert replayed[0].session_id == session.id
    assert replayed[0].data["webhook_event_id"] == "wh_evt_1"
    assert replayed[0].data["webhook_ingest_status"] == "reconcile_required"
    assert state is not None
    assert state.status == "completed"
    assert state.last_provider_event_id == "provider_evt_1"
    assert state.last_provider_cursor == "cursor_1"

    _ = await runtime.agents.ingest_webhook(
        provider="webhook-agent",
        headers={"x-provider-signature": "sig"},
        body=b'{"type":"session.status_updated"}',
    )
    replayed_again = await runtime.agents.replay(session)
    assert [event.id for event in replayed_again] == ["evt_provider_status_1"]
    await store.close()
