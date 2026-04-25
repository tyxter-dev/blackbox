from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar, cast
from uuid import uuid4

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactPage
from agent_runtime.core.errors import OutputValidationError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import RunItem
from agent_runtime.core.results import AgentResult, ToolPayload
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.core.state import ProviderState
from agent_runtime.core.stores import EventStore, InMemoryEventStore, InMemoryRunStore, RunStore
from agent_runtime.providers.base import AgentSpec, TaskSpec, TurnRequest, TurnResult
from agent_runtime.providers.registry import ProviderRef, ProviderRegistry
from agent_runtime.tools.registry import ToolCallable, ToolDefinition, ToolRegistry
from agent_runtime.tools.runtime import ToolRuntime

T = TypeVar("T")


@dataclass(slots=True)
class ModelRuntime:
    registry: ProviderRegistry

    async def stream(
        self,
        *,
        provider: str,
        model: str | None = None,
        input: str | list[object],
        provider_state: ProviderState | None = None,
        run_id: str | None = None,
        **kwargs: object,
    ) -> AsyncIterator[AgentEvent]:
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        request = TurnRequest(
            model=model_name,
            input=input,
            provider_state=provider_state,
            extra=dict(kwargs),
        )
        effective_run_id = run_id or f"run_{uuid4().hex}"
        sequence = 0
        async for event in adapter.stream_turn(request):
            yield replace(
                event,
                run_id=event.run_id or effective_run_id,
                sequence=event.sequence if event.sequence is not None else sequence,
            )
            sequence += 1

    async def run(
        self,
        *,
        provider: str,
        model: str | None = None,
        input: str | list[object],
        provider_state: ProviderState | None = None,
        run_id: str | None = None,
        **kwargs: object,
    ) -> TurnResult:
        events: list[AgentEvent] = []
        text_parts: list[str] = []
        captured_state: ProviderState | None = None
        async for event in self.stream(
            provider=provider,
            model=model,
            input=input,
            provider_state=provider_state,
            run_id=run_id,
            **kwargs,
        ):
            events.append(event)
            if event.type == EventTypes.MODEL_TEXT_DELTA:
                delta = event.data.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            maybe_state = event.data.get("provider_state")
            if isinstance(maybe_state, ProviderState):
                captured_state = maybe_state
        return TurnResult(
            text="".join(text_parts),
            events=events,
            provider_state=captured_state,
        )


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

    async def stream(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        adapter = self.registry.get_agent(session.provider)
        run_id = f"run_{uuid4().hex}"
        sequence = 0
        async for event in adapter.stream_events(session, after_event_id=after_event_id):
            yield replace(
                event,
                run_id=event.run_id or run_id,
                sequence=event.sequence if event.sequence is not None else sequence,
            )
            sequence += 1

    async def run(
        self,
        *,
        provider: str,
        agent: AgentRef | str,
        task: str | TaskSpec,
        model: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        session = await self.create_session(provider=provider, agent=agent, task=task, model=model)
        async for event in self.stream(session):
            yield event

    async def send_message(
        self, session: SessionRef | AgentSession, message: str
    ) -> InvocationRef:
        adapter = self.registry.get_agent(session.provider)
        return await adapter.send_message(session, message)

    async def approve(self, provider: str, approval_id: str, decision: ApprovalDecision) -> None:
        adapter = self.registry.get_agent(ProviderRef.parse(provider).provider_key)
        await adapter.approve(approval_id, decision)

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        adapter = self.registry.get_agent(session.provider)
        await adapter.cancel(session)

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        adapter = self.registry.get_agent(session.provider)
        return await adapter.list_artifacts(
            session, type=type, after=after, limit=limit,
        )


@dataclass(slots=True)
class ToolRuntimeFacade:
    """High-level facade exposing the local tool registry on AgentRuntime.

    Wraps a ``ToolRegistry`` and a default ``ToolRuntime`` so applications can
    register tools without touching the underlying classes.
    """

    registry: ToolRegistry = field(default_factory=ToolRegistry)
    default_runtime: ToolRuntime = field(init=False)

    def __post_init__(self) -> None:
        self.default_runtime = ToolRuntime(self.registry)

    def register(
        self,
        function: ToolCallable,
        *,
        name: str | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        blocking: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ToolDefinition:
        return self.registry.register(
            function,
            name=name,
            description=description,
            parameters=parameters,
            category=category,
            tags=tags,
            blocking=blocking,
            metadata=metadata,
        )

    def get(self, name: str) -> ToolDefinition:
        return self.registry.get(name)

    def all_tools(self) -> list[ToolDefinition]:
        return self.registry.all_tools()

    def to_provider_tools(self) -> list[dict[str, Any]]:
        return self.registry.to_provider_tools()

    async def call(
        self, name: str, arguments: dict[str, Any] | None = None, *, mock: bool = False
    ) -> Any:
        return await self.default_runtime.call(name, arguments, mock=mock)


class AgentRuntime:
    """Top-level runtime.

    Exposes:
      * ``runtime.run(...)`` and ``runtime.stream(...)`` — high-level blackbox
        loop that owns tool dispatch, continuation, and structured output
        validation. This is the spiritual successor to the v1 product promise.
      * ``runtime.tools`` — the local tool registry facade used by the
        high-level path.
      * ``runtime.models`` and ``runtime.agents`` — lower-level supervision
        facades for direct model turns and agent sessions.
    """

    def __init__(
        self,
        registry: ProviderRegistry | None = None,
        *,
        event_store: EventStore | None = None,
        run_store: RunStore | None = None,
    ) -> None:
        self.registry = registry or ProviderRegistry()
        self.event_store: EventStore = event_store or InMemoryEventStore()
        self.run_store: RunStore = run_store or InMemoryRunStore()
        self.models = ModelRuntime(self.registry)
        self.agents = AgentRuntimeFacade(self.registry)
        self.tools = ToolRuntimeFacade()

    async def stream(
        self,
        *,
        provider: str,
        input: str,
        model: str | None = None,
        tools: list[str] | None = None,
        tool_execution_context: dict[str, Any] | None = None,
        approval_policy: Any = None,
        policy: Any = None,
        max_iterations: int = 8,
        mock_tools: bool = False,
        **kwargs: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Stream events from a complete agent loop driven by the registered model.

        ``tools`` is a list of registered tool names to expose to the model.
        Local tool calls are dispatched through ``runtime.tools`` automatically
        and their results are fed back via provider-native continuation.
        """
        from agent_runtime.loop import AgentLoop

        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")

        tool_definitions = self._tool_payload(tools)
        tool_runtime = self._tool_runtime_for(tool_execution_context)

        async def stream_factory(
            *, input: str | list[Any], provider_state: ProviderState | None
        ) -> AsyncIterator[AgentEvent]:
            async for event in self.models.stream(
                provider=provider,
                model=model_name,
                input=input,
                provider_state=provider_state,
                tools=tool_definitions,
                **kwargs,
            ):
                yield event

        loop = AgentLoop(
            stream_factory=stream_factory,
            tools=tool_runtime if tools else None,
            approval_policy=approval_policy,
            policy=policy,
            max_iterations=max_iterations,
        )
        run_id = f"run_{uuid4().hex}"
        sequence = 0
        async for event in loop.run(
            input=input,
            provider_id=provider_ref.provider_key,
            mock_tools=mock_tools,
        ):
            stamped = replace(event, run_id=run_id, sequence=sequence)
            sequence += 1
            await self.event_store.append(stamped)
            yield stamped

    async def run(
        self,
        *,
        provider: str,
        input: str,
        model: str | None = None,
        tools: list[str] | None = None,
        tool_execution_context: dict[str, Any] | None = None,
        approval_policy: Any = None,
        policy: Any = None,
        max_iterations: int = 8,
        mock_tools: bool = False,
        output_type: type[T] | None = None,
        **kwargs: Any,
    ) -> AgentResult[T]:
        """Run the complete agent loop and return a typed AgentResult.

        When ``output_type`` is provided, the final text is validated against it.
        Pydantic models, dataclasses, and ``str`` are supported in v0.1.
        Validation failures raise :class:`OutputValidationError` (fail-fast).
        """
        events: list[AgentEvent] = []
        items: list[RunItem] = []
        artifacts: list[Artifact] = []
        payloads: list[ToolPayload] = []
        text_parts: list[str] = []
        captured_state: ProviderState | None = None

        async for event in self.stream(
            provider=provider,
            input=input,
            model=model,
            tools=tools,
            tool_execution_context=tool_execution_context,
            approval_policy=approval_policy,
            policy=policy,
            max_iterations=max_iterations,
            mock_tools=mock_tools,
            **kwargs,
        ):
            events.append(event)
            if event.type == EventTypes.MODEL_TEXT_DELTA:
                delta = event.data.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            elif event.type == EventTypes.TOOL_CALL_COMPLETED:
                if "payload" in event.data:
                    payloads.append(
                        ToolPayload(
                            tool_name=event.data.get("name", ""),
                            payload=event.data["payload"],
                            call_id=event.data.get("call_id"),
                        )
                    )
            item = event.data.get("item")
            if isinstance(item, RunItem):
                items.append(item)
            maybe_state = event.data.get("provider_state")
            if isinstance(maybe_state, ProviderState):
                captured_state = maybe_state

        text = "".join(text_parts)
        output = _validate_output(text, output_type)

        return AgentResult(
            output=cast(T, output),
            text=text,
            events=events,
            items=items,
            artifacts=artifacts,
            payloads=payloads,
            provider_state=captured_state,
        )

    def _tool_payload(self, names: list[str] | None) -> list[dict[str, Any]]:
        if not names:
            return []
        provider_tools = self.tools.to_provider_tools()
        wanted = set(names)
        return [t for t in provider_tools if t["name"] in wanted]

    def _tool_runtime_for(
        self, context: dict[str, Any] | None
    ) -> ToolRuntime:
        if context is None:
            return self.tools.default_runtime
        return ToolRuntime(self.tools.registry, context=context)


def _validate_output(text: str, output_type: type[Any] | None) -> Any:
    if output_type is None or output_type is str:
        return text
    pydantic_base: type[Any] | None
    try:
        from pydantic import BaseModel as _PydanticBaseModel
        pydantic_base = _PydanticBaseModel
    except ImportError:
        pydantic_base = None

    if (
        pydantic_base is not None
        and isinstance(output_type, type)
        and issubclass(output_type, pydantic_base)
    ):
        try:
            return output_type.model_validate_json(text)
        except Exception as exc:
            raise OutputValidationError(
                f"Final output failed validation against {output_type.__name__}: {exc}",
                raw_text=text,
                cause=exc,
            ) from exc

    import dataclasses
    if dataclasses.is_dataclass(output_type):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OutputValidationError(
                f"Final output is not valid JSON for {output_type.__name__}",
                raw_text=text,
                cause=exc,
            ) from exc
        try:
            return output_type(**data)
        except TypeError as exc:
            raise OutputValidationError(
                f"Final output cannot be coerced to {output_type.__name__}: {exc}",
                raw_text=text,
                cause=exc,
            ) from exc

    raise OutputValidationError(
        f"Unsupported output_type: {output_type!r}. Use str, a Pydantic model, or a dataclass.",
        raw_text=text,
    )
