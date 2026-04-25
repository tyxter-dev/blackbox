from __future__ import annotations

from dataclasses import dataclass, field

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.sessions import AgentRef, AgentSession, SessionRef
from agent_runtime.providers.base import AgentSpec, TaskSpec, TurnRequest, TurnResult
from agent_runtime.providers.registry import ProviderRegistry, ProviderRef


@dataclass(slots=True)
class ModelRuntime:
    registry: ProviderRegistry

    async def stream(
        self,
        *,
        provider: str,
        model: str | None = None,
        input: str | list[object],
        **kwargs: object,
    ):
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        request = TurnRequest(model=model_name, input=input, extra=dict(kwargs))
        async for event in adapter.stream_turn(request):
            yield event

    async def run(
        self,
        *,
        provider: str,
        model: str | None = None,
        input: str | list[object],
        **kwargs: object,
    ) -> TurnResult:
        events: list[AgentEvent] = []
        text_parts: list[str] = []
        async for event in self.stream(provider=provider, model=model, input=input, **kwargs):
            events.append(event)
            if event.type == EventTypes.MODEL_TEXT_DELTA:
                delta = event.data.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
        return TurnResult(text="".join(text_parts), events=events)


@dataclass(slots=True)
class AgentRuntimeFacade:
    registry: ProviderRegistry
    _sessions: dict[str, AgentSession] = field(default_factory=dict)

    async def create_agent(self, *, provider: str, spec: AgentSpec) -> AgentRef:
        adapter = self.registry.get_agent(ProviderRef.parse(provider).provider_key)
        return await adapter.create_agent(spec)

    async def create_session(
        self,
        *,
        provider: str,
        agent: AgentRef | str,
        task: str | TaskSpec,
        model: str | None = None,
    ) -> AgentSession:
        adapter = self.registry.get_agent(ProviderRef.parse(provider).provider_key)
        task_spec = task if isinstance(task, TaskSpec) else TaskSpec(prompt=task, model=model)
        session = await adapter.start_session(agent, task_spec)
        self._sessions[session.id] = session
        return session

    async def stream(self, session: SessionRef | AgentSession):
        adapter = self.registry.get_agent(session.provider)
        async for event in adapter.stream_events(session):
            yield event

    async def run(
        self,
        *,
        provider: str,
        agent: AgentRef | str,
        task: str | TaskSpec,
        model: str | None = None,
    ):
        session = await self.create_session(provider=provider, agent=agent, task=task, model=model)
        async for event in self.stream(session):
            yield event

    async def send_message(self, session: SessionRef | AgentSession, message: str) -> None:
        adapter = self.registry.get_agent(session.provider)
        await adapter.send_message(session, message)

    async def approve(self, provider: str, approval_id: str, decision: ApprovalDecision) -> None:
        adapter = self.registry.get_agent(ProviderRef.parse(provider).provider_key)
        await adapter.approve(approval_id, decision)

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        adapter = self.registry.get_agent(session.provider)
        await adapter.cancel(session)

    async def list_artifacts(self, session: SessionRef | AgentSession) -> list[Artifact]:
        adapter = self.registry.get_agent(session.provider)
        return await adapter.list_artifacts(session)


class AgentRuntime:
    """Top-level runtime.

    The two facades intentionally separate model turns from agent sessions.
    """

    def __init__(self, registry: ProviderRegistry | None = None) -> None:
        self.registry = registry or ProviderRegistry()
        self.models = ModelRuntime(self.registry)
        self.agents = AgentRuntimeFacade(self.registry)
