from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar, cast
from uuid import uuid4

from agent_runtime.core.accounting import ModelCatalog, add_usage, usage_to_dict
from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactPage
from agent_runtime.core.errors import OutputValidationError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import RunItem
from agent_runtime.core.results import AgentResult, OutputSpec, ToolPayload
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.core.state import ProviderState
from agent_runtime.core.stores import EventStore, InMemoryEventStore, InMemoryRunStore, RunStore
from agent_runtime.providers.base import (
    AgentSpec,
    ModelRequestControls,
    TaskSpec,
    TurnRequest,
    TurnResult,
)
from agent_runtime.providers.registry import ProviderRef, ProviderRegistry
from agent_runtime.tools.registry import ToolCallable, ToolDefinition, ToolRegistry
from agent_runtime.tools.results import ToolResult
from agent_runtime.tools.runtime import ToolRuntime
from agent_runtime.tools.session import ToolSession

T = TypeVar("T")


@dataclass(slots=True)
class ModelRuntime:
    registry: ProviderRegistry
    model_catalog: ModelCatalog = field(default_factory=ModelCatalog)

    async def stream(
        self,
        *,
        provider: str,
        model: str | None = None,
        input: str | Sequence[object],
        provider_state: ProviderState | None = None,
        tools: list[Any] | None = None,
        instructions: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        tool_choice: Any | None = None,
        parallel_tool_calls: bool | None = None,
        reasoning_effort: str | None = None,
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
            input=input if isinstance(input, str) else list(input),
            provider_state=provider_state,
            tools=list(tools or []),
            controls=ModelRequestControls(
                instructions=instructions,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
                reasoning_effort=reasoning_effort,
            ),
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
        input: str | Sequence[object],
        provider_state: ProviderState | None = None,
        tools: list[Any] | None = None,
        instructions: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        tool_choice: Any | None = None,
        parallel_tool_calls: bool | None = None,
        reasoning_effort: str | None = None,
        run_id: str | None = None,
        **kwargs: object,
    ) -> TurnResult:
        events: list[AgentEvent] = []
        text_parts: list[str] = []
        captured_state: ProviderState | None = None
        usage = None
        completed_provider: str | None = None
        completed_model: str | None = None
        async for event in self.stream(
            provider=provider,
            model=model,
            input=input,
            provider_state=provider_state,
            tools=tools,
            instructions=instructions,
            temperature=temperature,
            top_p=top_p,
            max_output_tokens=max_output_tokens,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_effort=reasoning_effort,
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
            if event.type == EventTypes.MODEL_COMPLETED:
                usage = add_usage(usage, event.data.get("usage"))
                completed_provider = event.provider or completed_provider
                model_value = event.data.get("model")
                if isinstance(model_value, str):
                    completed_model = model_value
        metadata: dict[str, Any] = {}
        usage_dict = usage_to_dict(usage)
        if usage_dict is not None:
            metadata["usage"] = usage_dict
        if usage is not None and completed_provider is not None and completed_model is not None:
            cost = self.model_catalog.estimate_cost(
                provider=completed_provider, model=completed_model, usage=usage
            )
            if cost is not None:
                metadata["cost"] = cost
        return TurnResult(
            text="".join(text_parts),
            events=events,
            provider_state=captured_state,
            metadata=metadata,
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

    def session(self) -> ToolSession:
        """Create an isolated tool registry seeded with the global tools."""
        return ToolSession.from_registry(self.registry)

    async def call(
        self, name: str, arguments: dict[str, Any] | None = None, *, mock: bool = False
    ) -> Any:
        return await self.default_runtime.call(name, arguments, mock=mock)

    def register_workspace(
        self,
        workspace: Any,
        ref: Any,
        *,
        prefix: str = "workspace",
    ) -> list[ToolDefinition]:
        """Register local workspace operations as model-callable tools.

        The returned tool names can be passed to ``AgentRuntime.run(...,
        tools=[...])`` so the existing local tool loop can read/write files,
        run commands, create snapshots, and emit the workspace runtime's
        canonical events.
        """
        from agent_runtime.workspaces.changes import CommandSpec, FileChange, Patch

        async def read_file(path: str) -> ToolResult:
            content = await workspace.read_file(ref, path)
            return ToolResult(
                content=content,
                payload={"path": path, "content": content},
                metadata={"workspace_id": ref.id},
            )

        async def write_file(path: str, content: str) -> ToolResult:
            change = await workspace.write_file(ref, path, content)
            return ToolResult(
                content=f"Wrote {path}.",
                payload={"change": change},
                metadata={"workspace_id": ref.id},
            )

        async def delete_file(path: str) -> ToolResult:
            change = await workspace.delete_file(ref, path)
            return ToolResult(
                content=f"Deleted {path}.",
                payload={"change": change},
                metadata={"workspace_id": ref.id},
            )

        async def apply_patch(
            summary: str,
            diff: str,
            changes: list[dict[str, Any]],
        ) -> ToolResult:
            patch = Patch(
                summary=summary,
                diff=diff,
                changes=[
                    FileChange(
                        path=str(change["path"]),
                        type=change["type"],
                        content=change.get("content"),
                        old_path=change.get("old_path"),
                    )
                    for change in changes
                ],
            )
            artifact = await workspace.apply_patch(ref, patch)
            return ToolResult(
                content=f"Created patch artifact {artifact.id}.",
                payload={"artifact": artifact},
                metadata={"workspace_id": ref.id},
            )

        async def run_command(
            command: str,
            cwd: str | None = None,
            timeout_seconds: float | None = None,
        ) -> ToolResult:
            result = await workspace.run_command(
                ref, CommandSpec(command=command, cwd=cwd, timeout=timeout_seconds)
            )
            return ToolResult(
                content=result.stdout or result.stderr or f"Command exited {result.exit_code}.",
                payload={"result": result},
                metadata={"workspace_id": ref.id, "exit_code": result.exit_code},
            )

        async def snapshot(name: str | None = None) -> ToolResult:
            artifact = await workspace.snapshot(ref, name=name)
            return ToolResult(
                content=f"Created workspace snapshot {artifact.id}.",
                payload={"artifact": artifact},
                metadata={"workspace_id": ref.id},
            )

        tool_specs: list[tuple[ToolCallable, str, str, dict[str, Any]]] = [
            (
                read_file,
                f"{prefix}_read_file",
                "Read a text file from the current workspace.",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            (
                write_file,
                f"{prefix}_write_file",
                "Write a text file in the current workspace.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
            (
                delete_file,
                f"{prefix}_delete_file",
                "Delete a file from the current workspace.",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            (
                apply_patch,
                f"{prefix}_apply_patch",
                "Apply a structured patch to the current workspace.",
                {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "diff": {"type": "string"},
                        "changes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "type": {
                                        "type": "string",
                                        "enum": ["create", "update", "delete"],
                                    },
                                    "content": {"type": "string"},
                                    "old_path": {"type": "string"},
                                },
                                "required": ["path", "type"],
                            },
                        },
                    },
                    "required": ["summary", "diff", "changes"],
                },
            ),
            (
                run_command,
                f"{prefix}_run_command",
                "Run a command in the current workspace.",
                {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "timeout_seconds": {"type": "number"},
                    },
                    "required": ["command"],
                },
            ),
            (
                snapshot,
                f"{prefix}_snapshot",
                "Create a snapshot artifact for the current workspace.",
                {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            ),
        ]
        return [
            self.register(
                function,
                name=name,
                description=description,
                parameters=parameters,
                category="workspace",
                tags=["workspace"],
                metadata={"workspace_id": ref.id},
            )
            for function, name, description, parameters in tool_specs
        ]


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
        self.model_catalog = ModelCatalog()
        self.event_store: EventStore = event_store or InMemoryEventStore()
        self.run_store: RunStore = run_store or InMemoryRunStore()
        self.models = ModelRuntime(self.registry, model_catalog=self.model_catalog)
        self.agents = AgentRuntimeFacade(self.registry)
        self.tools = ToolRuntimeFacade()

    async def stream(
        self,
        *,
        provider: str,
        input: str,
        model: str | None = None,
        tools: list[str] | None = None,
        tool_session: ToolSession | None = None,
        tool_execution_context: dict[str, Any] | None = None,
        approval_policy: Any = None,
        policy: Any = None,
        max_iterations: int = 8,
        mock_tools: bool = False,
        provider_state: ProviderState | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Stream events from a complete agent loop driven by the registered model.

        ``tools`` is a list of registered tool names to expose to the model.
        Local tool calls are dispatched through ``runtime.tools`` automatically
        and their results are fed back via provider-native continuation.

        Pass ``provider_state`` to resume from saved state (e.g. one loaded
        from :class:`SQLiteRunStore`).
        """
        from agent_runtime.loop import AgentLoop

        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")

        tool_definitions = self._tool_payload(tools, tool_session=tool_session)
        tool_runtime = self._tool_runtime_for(tool_execution_context, tool_session=tool_session)

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
            provider_state=provider_state,
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
        tool_session: ToolSession | None = None,
        tool_execution_context: dict[str, Any] | None = None,
        approval_policy: Any = None,
        policy: Any = None,
        max_iterations: int = 8,
        mock_tools: bool = False,
        output_type: type[T] | None = None,
        output_spec: OutputSpec | None = None,
        provider_state: ProviderState | None = None,
        **kwargs: Any,
    ) -> AgentResult[T]:
        """Run the complete agent loop and return a typed AgentResult.

        When ``output_type`` (or ``output_spec``) is provided, the final text is
        validated against the schema. Supported schemas: Pydantic models,
        dataclasses, and ``str``. Validation failures raise
        :class:`OutputValidationError`.

        Pass ``output_spec=OutputSpec(schema=Cls,
        strategy="posthoc_parse_with_retry", max_validation_retries=N)`` to ask
        the runtime to feed validation errors back to the model and retry up to
        ``N`` times before failing.
        """
        spec = _resolve_output_spec(output_type, output_spec)
        attempts_allowed = (
            1 + spec.max_validation_retries
            if spec.strategy == "posthoc_parse_with_retry"
            else 1
        )

        events: list[AgentEvent] = []
        items: list[RunItem] = []
        artifacts: list[Artifact] = []
        payloads: list[ToolPayload] = []
        captured_state: ProviderState | None = provider_state
        usage = None
        completed_provider: str | None = None
        completed_model: str | None = None
        last_text = ""
        last_error: OutputValidationError | None = None
        current_input = input

        for attempt in range(attempts_allowed):
            text_parts: list[str] = []
            async for event in self.stream(
                provider=provider,
                input=current_input,
                model=model,
                tools=tools,
                tool_session=tool_session,
                tool_execution_context=tool_execution_context,
                approval_policy=approval_policy,
                policy=policy,
                max_iterations=max_iterations,
                mock_tools=mock_tools,
                provider_state=captured_state,
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
                if event.type == EventTypes.MODEL_COMPLETED:
                    usage = add_usage(usage, event.data.get("usage"))
                    completed_provider = event.provider or completed_provider
                    model_value = event.data.get("model")
                    if isinstance(model_value, str):
                        completed_model = model_value

            last_text = "".join(text_parts)
            try:
                output = _validate_output(last_text, spec.schema)
            except OutputValidationError as exc:
                last_error = exc
                if attempt + 1 >= attempts_allowed:
                    raise
                current_input = _build_repair_prompt(
                    previous_text=last_text, error=exc, attempt=attempt + 1
                )
                continue

            metadata: dict[str, Any] = {"validation_attempts": attempt + 1}
            usage_dict = usage_to_dict(usage)
            if usage_dict is not None:
                metadata["usage"] = usage_dict
            if usage is not None and completed_provider is not None and completed_model is not None:
                cost = self.model_catalog.estimate_cost(
                    provider=completed_provider, model=completed_model, usage=usage
                )
                if cost is not None:
                    metadata["cost"] = cost
            return AgentResult(
                output=cast(T, output),
                text=last_text,
                events=events,
                items=items,
                artifacts=artifacts,
                payloads=payloads,
                provider_state=captured_state,
                metadata=metadata,
            )

        # Should be unreachable: the loop either returns or re-raises.
        assert last_error is not None
        raise last_error

    def _tool_payload(
        self, names: list[str] | None, *, tool_session: ToolSession | None = None
    ) -> list[dict[str, Any]]:
        if not names:
            return []
        provider_tools = (
            tool_session.to_provider_tools() if tool_session is not None else self.tools.to_provider_tools()
        )
        wanted = set(names)
        return [t for t in provider_tools if t["name"] in wanted]

    def _tool_runtime_for(
        self, context: dict[str, Any] | None, *, tool_session: ToolSession | None = None
    ) -> ToolRuntime:
        if tool_session is not None:
            return tool_session.runtime(context=context)
        if context is None:
            return self.tools.default_runtime
        return ToolRuntime(self.tools.registry, context=context)


def _resolve_output_spec(
    output_type: type[Any] | None, output_spec: OutputSpec | None
) -> OutputSpec:
    if output_type is not None and output_spec is not None:
        raise ValueError("Pass either output_type or output_spec, not both.")
    if output_spec is not None:
        return output_spec
    return OutputSpec(schema=output_type, strategy="posthoc_parse")


def _build_repair_prompt(
    *, previous_text: str, error: OutputValidationError, attempt: int
) -> str:
    return (
        "Your previous response failed schema validation.\n\n"
        f"Previous response:\n{previous_text}\n\n"
        f"Validation error: {error}\n\n"
        f"Please return a corrected response (attempt {attempt + 1}) that "
        "matches the requested schema exactly. Do not include any commentary "
        "outside the structured output."
    )


def _validate_output(text: str, output_type: type[Any] | dict[str, Any] | None) -> Any:
    if output_type is None or output_type is str:
        return text
    if isinstance(output_type, dict):
        raise OutputValidationError(
            "Dict-based JSON Schemas are not yet supported by posthoc_parse; "
            "pass a Pydantic model or dataclass.",
            raw_text=text,
        )
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
