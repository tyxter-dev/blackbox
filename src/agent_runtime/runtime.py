from __future__ import annotations

import builtins
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar, cast
from uuid import uuid4

from agent_runtime.compat.chat import ChatMessage, messages_to_input
from agent_runtime.core.accounting import (
    ModelCatalog,
    ModelUsage,
    add_usage,
    usage_provider_details,
    usage_to_dict,
)
from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactPage, ArtifactRef
from agent_runtime.core.cache import (
    InMemoryProviderCacheStore,
    ProviderCacheRecord,
    ProviderCacheStore,
    cache_usage_from_usage,
    record_provider_cache_usage,
)
from agent_runtime.core.capabilities import ModelCapabilityProfile, get_model_capability_profile
from agent_runtime.core.errors import (
    ApprovalError,
    ConfigurationError,
    OutputValidationError,
    ProviderExecutionError,
)
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import RunItem
from agent_runtime.core.prompts import (
    PromptBundle,
    PromptComposer,
    PromptFragment,
    PromptFragmentRegistry,
    PromptMode,
    PromptSpec,
)
from agent_runtime.core.results import (
    AgentResult,
    AgentSessionResult,
    AgentSessionResultStatus,
    OutputSpec,
    OutputStrategy,
    ToolPayload,
)
from agent_runtime.core.run_plan import (
    DataSourceRef,
    DynamicToolLoadingSpec,
    ResolvedMCPToolset,
    ResolvedRunSpec,
    ResolvedTool,
    resolved_hosted_tools,
)
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.core.state import ProviderState, RunState
from agent_runtime.core.stores import EventStore, InMemoryEventStore, InMemoryRunStore, RunStore
from agent_runtime.hosted_tools import HostedToolHandlers, HostedToolSpec, hosted_tool_kind
from agent_runtime.mcp import MCPConnector, MCPToolset, resolve_mcp_route, to_remote_mcp
from agent_runtime.models.capability_validation import (
    resolve_output_strategy,
    validate_turn_request_capabilities,
)
from agent_runtime.observability.traces import TraceContext, trace_metadata_from_events
from agent_runtime.output.schema import OutputSchema, build_output_schema
from agent_runtime.providers.base import (
    AgentSpec,
    CompactionControl,
    ModelCacheControl,
    ModelRequestControls,
    TaskSpec,
    ToolSearchControl,
    TurnRequest,
    TurnResult,
)
from agent_runtime.providers.registry import ProviderRef, ProviderRegistry
from agent_runtime.tools.registry import ToolCallable, ToolDefinition, ToolRegistry
from agent_runtime.tools.runtime import ToolRuntime
from agent_runtime.tools.session import ToolSession

T = TypeVar("T")
_FINALIZER_TOOL_NAME = "submit_final_output"


@dataclass(slots=True)
class ModelRuntime:
    registry: ProviderRegistry
    model_catalog: ModelCatalog = field(default_factory=ModelCatalog)
    provider_cache_store: ProviderCacheStore = field(
        default_factory=InMemoryProviderCacheStore
    )

    def capabilities(
        self,
        provider: str,
        *,
        model: str | None = None,
    ) -> ModelCapabilityProfile:
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        adapter = self.registry.get_model(provider_ref.provider_key)
        return get_model_capability_profile(adapter, model_name)

    async def stream(
        self,
        *,
        provider: str,
        model: str | None = None,
        input: str | Sequence[object],
        provider_state: ProviderState | None = None,
        tools: list[Any] | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        hosted_tool_handlers: HostedToolHandlers | None = None,
        output_schema: OutputSchema | None = None,
        output_strategy: OutputStrategy | None = None,
        instructions: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        tool_choice: Any | None = None,
        parallel_tool_calls: bool | None = None,
        reasoning_effort: str | None = None,
        cache: ModelCacheControl | None = None,
        verbosity: str | None = None,
        reasoning_summary: str | None = None,
        state_mode: str | None = None,
        background: bool | None = None,
        store: bool | None = None,
        include: list[str] | None = None,
        tool_search: ToolSearchControl | None = None,
        compaction: CompactionControl | None = None,
        modalities: list[str] | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
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
            hosted_tools=list(hosted_tools or []),
            output_schema=output_schema,
            output_strategy=output_strategy,
            controls=ModelRequestControls(
                instructions=instructions,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
                reasoning_effort=reasoning_effort,
                cache=cache,
                verbosity=verbosity,
                reasoning_summary=reasoning_summary,
                state_mode=cast(Any, state_mode),
                background=background,
                store=store,
                include=include,
                tool_search=tool_search,
                compaction=compaction,
                modalities=modalities,
            ),
            extra=dict(kwargs),
        )
        validate_turn_request_capabilities(provider=adapter, request=request)
        effective_run_id = run_id or f"run_{uuid4().hex}"
        trace_context = TraceContext.for_run(
            trace_id or effective_run_id,
            root_span_id=parent_span_id,
        )
        sequence = 0
        async for event in adapter.stream_turn(request):
            yield trace_context.stamp(
                event,
                run_id=effective_run_id,
                sequence=sequence,
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
        hosted_tools: list[HostedToolSpec] | None = None,
        hosted_tool_handlers: HostedToolHandlers | None = None,
        output_schema: OutputSchema | None = None,
        output_strategy: OutputStrategy | None = None,
        instructions: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        tool_choice: Any | None = None,
        parallel_tool_calls: bool | None = None,
        reasoning_effort: str | None = None,
        cache: ModelCacheControl | None = None,
        verbosity: str | None = None,
        reasoning_summary: str | None = None,
        state_mode: str | None = None,
        background: bool | None = None,
        store: bool | None = None,
        include: list[str] | None = None,
        tool_search: ToolSearchControl | None = None,
        compaction: CompactionControl | None = None,
        modalities: list[str] | None = None,
        run_id: str | None = None,
        **kwargs: object,
    ) -> TurnResult:
        events: list[AgentEvent] = []
        text_parts: list[str] = []
        artifacts: list[Artifact] = []
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
            hosted_tools=hosted_tools,
            hosted_tool_handlers=hosted_tool_handlers,
            output_schema=output_schema,
            output_strategy=output_strategy,
            instructions=instructions,
            temperature=temperature,
            top_p=top_p,
            max_output_tokens=max_output_tokens,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_effort=reasoning_effort,
            cache=cache,
            verbosity=verbosity,
            reasoning_summary=reasoning_summary,
            state_mode=state_mode,
            background=background,
            store=store,
            include=include,
            tool_search=tool_search,
            compaction=compaction,
            modalities=modalities,
            run_id=run_id,
            **cast(Any, kwargs),
        ):
            events.append(event)
            if event.type == EventTypes.MODEL_TEXT_DELTA:
                delta = event.data.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            artifact = event.data.get("artifact")
            if isinstance(artifact, Artifact):
                artifacts.append(artifact)
            maybe_state = event.data.get("provider_state")
            if isinstance(maybe_state, ProviderState):
                captured_state = maybe_state
            if event.type == EventTypes.MODEL_COMPLETED:
                usage = add_usage(usage, _event_usage(event))
                completed_provider = event.provider or completed_provider
                model_value = event.data.get("model")
                if isinstance(model_value, str):
                    completed_model = model_value
        usage = add_usage(usage, _tool_usage_from_events(events))
        metadata: dict[str, Any] = {}
        usage_dict = usage_to_dict(usage)
        if usage_dict is not None:
            metadata["usage"] = usage_dict
        provider_usage = usage_provider_details(usage)
        if provider_usage is not None:
            metadata["usage_provider_details"] = provider_usage
        if usage is not None and completed_provider is not None and completed_model is not None:
            cost = self.model_catalog.estimate_cost(
                provider=completed_provider, model=completed_model, usage=usage
            )
            if cost is not None:
                metadata["cost"] = cost
        cache_metadata = await _provider_cache_metadata(
            provider=completed_provider,
            model=completed_model,
            cache=cache,
            usage=usage,
            provider_cache_store=self.provider_cache_store,
        )
        if cache_metadata:
            metadata["cache"] = cache_metadata
        mcp_metadata = _mcp_metadata_from_events(events)
        if mcp_metadata:
            metadata["mcp"] = mcp_metadata
        hosted_metadata = _hosted_tool_metadata_from_events(events)
        if hosted_metadata:
            metadata["hosted_tools"] = hosted_metadata
        workspace_metadata = _workspace_metadata_from_events(events)
        if workspace_metadata:
            metadata["workspaces"] = workspace_metadata
        prompt_metadata = _prompt_metadata_from_events(events)
        if prompt_metadata:
            metadata["prompt"] = prompt_metadata
        trace_metadata = trace_metadata_from_events(events, metadata=metadata)
        if trace_metadata["spans"]:
            metadata["trace"] = trace_metadata
        _attach_accounting_metadata(metadata)
        return TurnResult(
            text="".join(text_parts),
            events=events,
            provider_state=captured_state,
            artifacts=artifacts,
            metadata=metadata,
        )


@dataclass(slots=True)
class WorkspaceRuntimeFacade:
    """Provider registry and lifecycle facade for first-class workspaces."""

    providers: dict[str, Any] = field(default_factory=dict)
    _kind_providers: dict[str, str] = field(default_factory=dict)
    _workspace_providers: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from agent_runtime.workspaces import (
            CloudWorkspaceProvider,
            GitWorkspaceProvider,
            LocalWorkspaceProvider,
        )

        self.register(LocalWorkspaceProvider(), kinds=["local"])
        self.register(GitWorkspaceProvider(), kinds=["git"])
        self.register(CloudWorkspaceProvider(), kinds=["cloud"])

    def register(self, provider: Any, *, kinds: list[str] | None = None) -> Any:
        provider_id = str(getattr(provider, "provider_id", type(provider).__name__))
        self.providers[provider_id] = provider
        for kind in kinds or self._infer_kinds(provider):
            self._kind_providers[kind] = provider_id
        return provider

    async def open(
        self,
        spec: Any,
        *,
        provider: str | Any | None = None,
        policy: Any = None,
    ) -> Any:
        from agent_runtime.workspaces import WorkspaceRef, WorkspaceSpec

        if isinstance(spec, WorkspaceRef):
            workspace_provider = self.provider_for(spec, provider=provider)
            self._workspace_providers[spec.id] = workspace_provider
            return spec
        if not isinstance(spec, WorkspaceSpec):
            raise ConfigurationError("runtime.workspaces.open requires a WorkspaceSpec.")
        workspace_provider = self._provider_for_spec(spec, provider=provider, policy=policy)
        ws = await workspace_provider.open(spec)
        self._workspace_providers[ws.id] = workspace_provider
        self.providers.setdefault(workspace_provider.provider_id, workspace_provider)
        return ws

    async def attach(
        self,
        state: Any,
        *,
        provider: str | Any | None = None,
        policy: Any = None,
    ) -> Any:
        workspace_provider = self._provider_for_kind(
            state.kind,
            provider=provider or state.provider,
            policy=policy,
        )
        ws = await workspace_provider.attach(state)
        self._workspace_providers[ws.id] = workspace_provider
        return ws

    async def resolve(
        self,
        workspace: Any | None,
        *,
        provider: str | Any | None = None,
        policy: Any = None,
    ) -> tuple[Any | None, Any | None, bool]:
        from agent_runtime.workspaces import WorkspaceRef, WorkspaceSpec

        if workspace is None:
            return None, None, False
        if isinstance(workspace, WorkspaceRef):
            workspace_provider = self.provider_for(workspace, provider=provider)
            self._workspace_providers[workspace.id] = workspace_provider
            return workspace, workspace_provider, False
        if isinstance(workspace, WorkspaceSpec):
            ws = await self.open(workspace, provider=provider, policy=policy)
            return ws, self.provider_for(ws), True
        raise ConfigurationError("workspace must be a WorkspaceSpec or WorkspaceRef.")

    def provider_for(self, workspace: Any, *, provider: str | Any | None = None) -> Any:
        if provider is not None:
            return self._coerce_provider(provider)
        workspace_id = getattr(workspace, "id", None)
        if isinstance(workspace_id, str) and workspace_id in self._workspace_providers:
            return self._workspace_providers[workspace_id]
        provider_id = getattr(workspace, "provider", None)
        if isinstance(provider_id, str) and provider_id in self.providers:
            return self.providers[provider_id]
        raise ConfigurationError(
            "workspace provider is unknown; open the workspace through runtime.workspaces "
            "or pass workspace_provider explicitly."
        )

    async def close(self, workspace: Any, *, delete: bool = False) -> None:
        provider = self.provider_for(workspace)
        await provider.close(workspace, delete=delete)
        self._workspace_providers.pop(workspace.id, None)

    async def read_file(self, workspace: Any, path: str) -> str:
        return cast(str, await self.provider_for(workspace).read_file(workspace, path))

    async def write_file(self, workspace: Any, path: str, content: str) -> Any:
        return await self.provider_for(workspace).write_file(workspace, path, content)

    async def delete_file(self, workspace: Any, path: str) -> Any:
        return await self.provider_for(workspace).delete_file(workspace, path)

    async def list_files(
        self,
        workspace: Any,
        *,
        path: str = ".",
        recursive: bool = False,
        limit: int = 1000,
    ) -> list[str]:
        return cast(
            list[str],
            await self.provider_for(workspace).list_files(
                workspace,
                path=path,
                recursive=recursive,
                limit=limit,
            ),
        )

    async def apply_patch(self, workspace: Any, patch: Any) -> Any:
        return await self.provider_for(workspace).apply_patch(workspace, patch)

    async def run_command(self, workspace: Any, spec: Any) -> Any:
        return await self.provider_for(workspace).run_command(workspace, spec)

    async def snapshot(self, workspace: Any, *, name: str | None = None) -> Artifact:
        return cast(Artifact, await self.provider_for(workspace).snapshot(workspace, name=name))

    async def list_artifacts(
        self,
        workspace: Any,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        return cast(
            ArtifactPage,
            await self.provider_for(workspace).list_artifacts(
                workspace,
                type=type,
                after=after,
                limit=limit,
            ),
        )

    async def export_artifact(self, workspace: Any, ref: ArtifactRef) -> Artifact:
        return cast(Artifact, await self.provider_for(workspace).export_artifact(workspace, ref))

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        last_error: Exception | None = None
        providers = [*self.providers.values(), *self._workspace_providers.values()]
        seen: set[int] = set()
        for provider in providers:
            identity = id(provider)
            if identity in seen:
                continue
            seen.add(identity)
            approve = getattr(provider, "approve", None)
            if not callable(approve):
                continue
            try:
                await approve(approval_id, decision)
                return
            except Exception as exc:  # pragma: no cover - provider-specific miss
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ApprovalError(f"No pending approval with id '{approval_id}'.")

    def drain_events(self, workspace: Any | None = None) -> list[AgentEvent]:
        providers = (
            [self.provider_for(workspace)]
            if workspace is not None
            else [*self.providers.values(), *self._workspace_providers.values()]
        )
        events: list[AgentEvent] = []
        seen: set[int] = set()
        for provider in providers:
            identity = id(provider)
            if identity in seen:
                continue
            seen.add(identity)
            drain = getattr(provider, "drain_events", None)
            if callable(drain):
                events.extend(drain())
        return events

    def _provider_for_spec(self, spec: Any, *, provider: str | Any | None, policy: Any) -> Any:
        return self._provider_for_kind(spec.kind, provider=provider, policy=policy, spec=spec)

    def _provider_for_kind(
        self,
        kind: str,
        *,
        provider: str | Any | None = None,
        policy: Any = None,
        spec: Any | None = None,
    ) -> Any:
        if provider is not None:
            return self._coerce_provider(provider)
        if policy is not None:
            fresh = self._fresh_provider(kind, policy=policy, spec=spec)
            if fresh is not None:
                return fresh
        provider_id = self._kind_providers.get(kind)
        if provider_id is None:
            raise ConfigurationError(
                f"workspace_provider is required for {kind!r} workspaces."
            )
        return self.providers[provider_id]

    def _coerce_provider(self, provider: str | Any) -> Any:
        if isinstance(provider, str):
            try:
                return self.providers[provider]
            except KeyError as exc:
                raise ConfigurationError(f"Unknown workspace provider {provider!r}.") from exc
        self.register(provider)
        return provider

    def _fresh_provider(self, kind: str, *, policy: Any, spec: Any | None) -> Any | None:
        from agent_runtime.workspaces import (
            CloudWorkspaceProvider,
            GitWorkspaceProvider,
            LocalWorkspaceProvider,
        )

        if kind == "local":
            return LocalWorkspaceProvider(policy=policy)
        if kind == "git":
            return GitWorkspaceProvider(policy=policy)
        if kind == "cloud":
            return CloudWorkspaceProvider(policy=policy)
        if kind == "docker" and spec is not None and getattr(spec, "image", None):
            from agent_runtime.workspaces import DockerWorkspaceProvider

            return DockerWorkspaceProvider(image=spec.image, policy=policy)
        return None

    def _infer_kinds(self, provider: Any) -> list[str]:
        capabilities = provider.capabilities() if callable(getattr(provider, "capabilities", None)) else None
        kinds: list[str] = []
        if getattr(capabilities, "supports_local_files", False):
            kinds.append("local")
        if getattr(capabilities, "supports_git_sources", False):
            kinds.append("git")
        if getattr(capabilities, "supports_sandbox", False):
            kinds.append("sandbox")
        if getattr(capabilities, "supports_docker", False):
            kinds.append("docker")
        if getattr(capabilities, "supports_cloud_refs", False):
            kinds.append("cloud")
        return kinds


@dataclass(slots=True)
class AgentRuntimeFacade:
    registry: ProviderRegistry
    event_store: EventStore | None = None
    run_store: RunStore | None = None
    workspaces: WorkspaceRuntimeFacade | None = None
    _sessions: dict[str, AgentSession] = field(default_factory=dict)
    _session_workspaces: dict[str, tuple[Any, Any, bool]] = field(default_factory=dict)

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
        workspace: Any | None = None,
        workspace_provider: str | Any | None = None,
        workspace_policy: Any = None,
        workspace_preserve: bool = False,
        inputs: list[ArtifactRef] | None = None,
        metadata: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AgentSession:
        adapter = self.registry.get_agent(ProviderRef.parse(provider).provider_key)
        task_spec = _agent_task_spec(
            task,
            model=model,
            workspace=workspace,
            inputs=inputs,
            metadata=metadata,
            extra=extra,
        )
        resolved_workspace: tuple[Any, Any, bool] | None = None
        if task_spec.workspace is not None:
            if self.workspaces is None:
                raise ConfigurationError("AgentRuntimeFacade has no workspace runtime.")
            workspace_ref, provider_obj, opened = await self.workspaces.resolve(
                task_spec.workspace,
                provider=workspace_provider,
                policy=workspace_policy,
            )
            task_spec = replace(
                task_spec,
                workspace=workspace_ref,
                metadata={
                    **task_spec.metadata,
                    "workspace": _workspace_ref_metadata(workspace_ref),
                },
            )
            resolved_workspace = (provider_obj, workspace_ref, opened and not workspace_preserve)
        session = await adapter.start_session(agent, task_spec)
        if resolved_workspace is not None:
            provider_obj, workspace_ref, should_close = resolved_workspace
            self._session_workspaces[session.id] = (provider_obj, workspace_ref, should_close)
            session.metadata.setdefault("workspace", _workspace_ref_metadata(workspace_ref))
            session.metadata.setdefault("workspace_provider", getattr(provider_obj, "provider_id", None))
        self._sessions[session.id] = session
        return session

    async def stream(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        adapter = self.registry.get_agent(session.provider)
        effective_run_id = run_id or f"run_{uuid4().hex}"
        trace_context = TraceContext.for_run(
            trace_id or effective_run_id,
            root_span_id=parent_span_id,
        )
        sequence = 0
        workspace_context = self._session_workspaces.get(session.id)
        if workspace_context is not None:
            workspace_provider, workspace_ref, _ = workspace_context
            for workspace_event in _drain_workspace_events(workspace_provider, workspace_ref):
                stamped = trace_context.stamp(
                    workspace_event,
                    run_id=effective_run_id,
                    sequence=sequence,
                )
                sequence += 1
                if self.event_store is not None:
                    await self.event_store.append(stamped)
                yield stamped
        async for event in adapter.stream_events(session, after_event_id=after_event_id):
            if workspace_context is not None:
                _, workspace_ref, _ = workspace_context
                event = _agent_event_with_workspace(event, workspace_ref)
            stamped = trace_context.stamp(
                event,
                run_id=effective_run_id,
                sequence=sequence,
            )
            sequence += 1
            if self.event_store is not None:
                await self.event_store.append(stamped)
            yield stamped
        if workspace_context is not None:
            workspace_provider, workspace_ref, should_close = workspace_context
            session_obj = self._sessions.get(session.id)
            terminal_status = getattr(session_obj or session, "status", None)
            if should_close and terminal_status in {"completed", "failed", "cancelled"}:
                await workspace_provider.close(workspace_ref)
                self._session_workspaces.pop(session.id, None)
                for workspace_event in _drain_workspace_events(workspace_provider, workspace_ref):
                    stamped = trace_context.stamp(
                        workspace_event,
                        run_id=effective_run_id,
                        sequence=sequence,
                    )
                    sequence += 1
                    if self.event_store is not None:
                        await self.event_store.append(stamped)
                    yield stamped

    async def run(
        self,
        *,
        provider: str,
        agent: AgentRef | str,
        task: str | TaskSpec,
        model: str | None = None,
        workspace: Any | None = None,
        workspace_provider: str | Any | None = None,
        workspace_policy: Any = None,
        workspace_preserve: bool = False,
        inputs: list[ArtifactRef] | None = None,
        metadata: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        output_type: type[T] | None = None,
        output_spec: OutputSpec | None = None,
        artifact_type: str | None = None,
        artifact_limit: int = 100,
        collect_artifacts: bool = True,
        run_id: str | None = None,
    ) -> AgentSessionResult[T]:
        """Run a provider-managed agent session and collect the useful result."""
        session = await self.create_session(
            provider=provider,
            agent=agent,
            task=task,
            model=model,
            workspace=workspace,
            workspace_provider=workspace_provider,
            workspace_policy=workspace_policy,
            workspace_preserve=workspace_preserve,
            inputs=inputs,
            metadata=metadata,
            extra=extra,
        )
        events: list[AgentEvent] = []
        text_parts: list[str] = []
        terminal_text: str | None = None
        artifacts: list[Artifact] = []
        artifact_ids: set[str] = set()
        captured_state: ProviderState | None = None
        usage: ModelUsage | None = None

        async for event in self.stream(session, run_id=run_id):
            events.append(event)
            delta = _agent_event_text_delta(event)
            if delta is not None:
                text_parts.append(delta)
            if event.type == EventTypes.SESSION_COMPLETED:
                terminal_text = _agent_event_text(event) or terminal_text
            artifact = _artifact_from_event(event)
            if artifact is not None and (
                artifact_type is None or artifact.type == artifact_type
            ):
                _append_artifact_unique(artifacts, artifact_ids, artifact)
            maybe_state = event.data.get("provider_state")
            if isinstance(maybe_state, ProviderState):
                captured_state = maybe_state
            usage = add_usage(usage, _event_usage(event))

        if collect_artifacts:
            for artifact in await self._collect_session_artifacts(
                session,
                type=artifact_type,
                limit=artifact_limit,
            ):
                _append_artifact_unique(artifacts, artifact_ids, artifact)

        text = terminal_text if terminal_text is not None else "".join(text_parts)
        spec = _resolve_output_spec(output_type, output_spec)
        output = _validate_output(text, spec.schema, allow_dict_schema=True)
        usage = add_usage(usage, _tool_usage_from_events(events))
        result_status = _agent_session_result_status(session, events)
        provider_state = captured_state or _provider_state_from_session(session, events=events)
        usage_dict = usage_to_dict(usage)
        trace = trace_metadata_from_events(events, metadata={"usage": usage_dict} if usage_dict else {})
        session_ref = session.ref
        result_metadata: dict[str, Any] = {
            "status": result_status,
            "session": {
                "provider": session_ref.provider,
                "id": session_ref.id,
                "agent_id": session_ref.agent_id,
                "status": result_status,
                "lifecycle_status": session.status,
            },
            "event_count": len(events),
            "artifact_count": len(artifacts),
        }
        if usage_dict is not None:
            result_metadata["usage"] = usage_dict
        provider_usage = usage_provider_details(usage)
        if provider_usage is not None:
            result_metadata["usage_provider_details"] = provider_usage
        if trace["spans"]:
            result_metadata["trace"] = trace
        workspace_metadata = _workspace_metadata_from_events(events)
        if workspace_metadata:
            result_metadata["workspaces"] = workspace_metadata
        _attach_accounting_metadata(result_metadata)

        if self.run_store is not None:
            await self.run_store.save(
                RunState(
                    session_id=session.id,
                    provider=session.provider,
                    model=session.model,
                    provider_state=provider_state,
                    metadata={
                        "run_id": events[0].run_id if events else None,
                        "session": result_metadata["session"],
                        "last_event_id": events[-1].id if events else None,
                        "status": result_status,
                        "lifecycle_status": session.status,
                    },
                )
            )

        return AgentSessionResult(
            output=cast(T, output),
            text=text,
            session_ref=session_ref,
            status=result_status,
            events=events,
            artifacts=artifacts,
            provider_state=provider_state,
            usage=usage,
            trace=trace,
            metadata=result_metadata,
        )

    async def _collect_session_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None,
        limit: int,
    ) -> list[Artifact]:
        if limit <= 0:
            return []
        collected: list[Artifact] = []
        after: str | None = None
        seen_cursors: set[str] = set()
        while len(collected) < limit:
            page_limit = min(100, limit - len(collected))
            page = await self.list_artifacts(
                session,
                type=type,
                after=after,
                limit=page_limit,
            )
            collected.extend(page.items)
            if not page.has_more or not page.items:
                break
            next_after = page.next_cursor or page.items[-1].id
            if next_after in seen_cursors:
                break
            seen_cursors.add(next_after)
            after = next_after
        return collected

    async def send_message(
        self, session: SessionRef | AgentSession, message: str
    ) -> InvocationRef:
        adapter = self.registry.get_agent(session.provider)
        return await adapter.send_message(session, message)

    async def approve(self, provider: str, approval_id: str, decision: ApprovalDecision) -> None:
        adapter = self.registry.get_agent(ProviderRef.parse(provider).provider_key)
        adapter_error: Exception | None = None
        try:
            await adapter.approve(approval_id, decision)
            return
        except Exception as exc:
            adapter_error = exc
            if self.workspaces is None:
                raise
        if approval_id.startswith("workspace_approval_"):
            await self.workspaces.approve(approval_id, decision)
            return
        if adapter_error is not None:
            raise adapter_error
        raise ApprovalError(f"No pending approval with id '{approval_id}'.")

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
class ChatRuntimeFacade:
    """Explicit chat compatibility projection over the model runtime."""

    models: ModelRuntime

    async def stream(
        self,
        *,
        provider: str,
        messages: list[ChatMessage | dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[AgentEvent]:
        async for event in self.models.stream(
            provider=provider,
            model=model,
            input=messages_to_input(messages),
            **kwargs,
        ):
            yield event

    async def run(
        self,
        *,
        provider: str,
        messages: list[ChatMessage | dict[str, Any]],
        model: str | None = None,
        **kwargs: Any,
    ) -> TurnResult:
        return await self.models.run(
            provider=provider,
            model=model,
            input=messages_to_input(messages),
            **kwargs,
        )


@dataclass(slots=True)
class PromptRuntimeFacade:
    """Dry-run prompt composition facade over ``AgentRuntime.plan_run``."""

    runtime: Any

    async def build(self, **kwargs: Any) -> PromptBundle:
        plan = await self.runtime.plan_run(**kwargs)
        if plan.prompt is None:
            raise ConfigurationError("Prompt plan did not produce a prompt bundle.")
        return plan.prompt


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
        prompt_fragments: list[PromptFragment] | None = None,
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
            prompt_fragments=prompt_fragments,
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
        """Register provider-backed workspace operations as model-callable tools."""
        from agent_runtime.workspaces.tools import register_workspace_tools

        return register_workspace_tools(self.register, workspace, ref, prefix=prefix)


@dataclass(slots=True)
class ProviderCacheRuntime:
    """Facade for provider cache lifecycle and local cache registry operations."""

    registry: ProviderRegistry
    store: ProviderCacheStore

    async def create(
        self,
        *,
        provider: str,
        contents: Any,
        model: str | None = None,
        key: str | None = None,
        system_instruction: str | None = None,
        ttl: str | None = None,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ProviderCacheRecord:
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        create_cache = getattr(adapter, "create_cache", None)
        if not callable(create_cache):
            raise ConfigurationError(
                f"Provider '{provider_ref.provider_key}' does not expose provider-side cache creation."
            )
        result = create_cache(
            model=model_name,
            contents=contents,
            key=key,
            system_instruction=system_instruction,
            ttl=ttl,
            display_name=display_name,
            metadata=metadata,
            **kwargs,
        )
        record = await result if hasattr(result, "__await__") else result
        if not isinstance(record, ProviderCacheRecord):
            raise ProviderExecutionError(
                f"Provider '{provider_ref.provider_key}' returned an invalid cache record."
            )
        if key is not None and record.key is None:
            record.key = key
        if metadata:
            record.metadata.update(metadata)
        await self.store.save(record)
        return record

    async def get(
        self,
        *,
        provider: str,
        key: str | None = None,
        model: str | None = None,
        provider_cache_id: str | None = None,
    ) -> ProviderCacheRecord | None:
        provider_ref = ProviderRef.parse(provider)
        if provider_cache_id is not None:
            return await self.store.load_by_provider_cache_id(
                provider=provider_ref.provider_key,
                provider_cache_id=provider_cache_id,
            )
        if key is None:
            raise ValueError("Pass key or provider_cache_id.")
        return await self.store.load(
            provider=provider_ref.provider_key,
            model=model or provider_ref.resource,
            key=key,
        )

    async def list(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        include_expired: bool = True,
    ) -> builtins.list[ProviderCacheRecord]:
        provider_key = ProviderRef.parse(provider).provider_key if provider else None
        return await self.store.list_records(
            provider=provider_key,
            model=model,
            include_expired=include_expired,
        )

    async def invalidate(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        key: str | None = None,
        provider_cache_id: str | None = None,
        record_id: str | None = None,
        delete_provider_cache: bool = False,
    ) -> builtins.list[ProviderCacheRecord]:
        provider_key = ProviderRef.parse(provider).provider_key if provider else None
        deleted = await self.store.delete(
            provider=provider_key,
            model=model,
            key=key,
            provider_cache_id=provider_cache_id,
            record_id=record_id,
        )
        if delete_provider_cache:
            await self._delete_provider_caches(deleted)
        return deleted

    async def evict_expired(
        self, *, delete_provider_cache: bool = False
    ) -> builtins.list[ProviderCacheRecord]:
        evicted = await self.store.evict_expired()
        if delete_provider_cache:
            await self._delete_provider_caches(evicted)
        return evicted

    async def _delete_provider_caches(
        self, records: builtins.list[ProviderCacheRecord]
    ) -> None:
        for record in records:
            if record.provider_cache_id is None:
                continue
            adapter = self.registry.get_model(record.provider)
            delete_cache = getattr(adapter, "delete_cache", None)
            if not callable(delete_cache):
                continue
            result = delete_cache(record.provider_cache_id)
            if hasattr(result, "__await__"):
                await result


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
        provider_cache_store: ProviderCacheStore | None = None,
    ) -> None:
        self.registry = registry or ProviderRegistry()
        self.model_catalog = ModelCatalog()
        self.event_store: EventStore = event_store or InMemoryEventStore()
        self.run_store: RunStore = run_store or InMemoryRunStore()
        self.provider_cache_store: ProviderCacheStore = (
            provider_cache_store or InMemoryProviderCacheStore()
        )
        self.models = ModelRuntime(
            self.registry,
            model_catalog=self.model_catalog,
            provider_cache_store=self.provider_cache_store,
        )
        self.prompt_fragments = PromptFragmentRegistry()
        self.prompt_composer = PromptComposer(self.prompt_fragments)
        self.prompts = PromptRuntimeFacade(self)
        self.chat = ChatRuntimeFacade(self.models)
        self.workspaces = WorkspaceRuntimeFacade()
        self.tools = ToolRuntimeFacade()
        self.agents = AgentRuntimeFacade(
            self.registry,
            event_store=self.event_store,
            run_store=self.run_store,
            workspaces=self.workspaces,
        )
        self.caches = ProviderCacheRuntime(self.registry, self.provider_cache_store)
        from agent_runtime.realtime.runtime import RealtimeRuntime

        self.realtime = RealtimeRuntime(
            self.registry,
            event_store=self.event_store,
            tool_registry=self.tools.registry,
        )
        self._active_loops: dict[str, Any] = {}

    async def close(self) -> None:
        """Release resources held by registered provider adapters."""
        await self.registry.close()

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        """Approve a pending high-level ``runtime.run/stream`` operation."""
        for loop in list(self._active_loops.values()):
            if approval_id in loop._approvals:
                await loop.approve(approval_id, decision)
                return
        if approval_id.startswith("workspace_approval_"):
            try:
                await self.workspaces.approve(approval_id, decision)
                return
            except Exception:
                pass
        try:
            await self.realtime.approve(approval_id, decision)
            return
        except ApprovalError:
            pass
        raise ApprovalError(f"No pending approval with id '{approval_id}'.")

    def model_capabilities(
        self,
        provider: str,
        *,
        model: str | None = None,
    ) -> ModelCapabilityProfile:
        return self.models.capabilities(provider, model=model)

    async def plan_run(
        self,
        *,
        provider: str,
        input: str,
        model: str | None = None,
        tools: list[str] | None = None,
        tool_session: ToolSession | None = None,
        instructions: str | None = None,
        prompt: PromptSpec | None = None,
        prompt_mode: PromptMode | None = None,
        channel: str | None = None,
        output_schema: OutputSchema | None = None,
        output_strategy: OutputStrategy | None = None,
        output_spec: OutputSpec | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        toolsets: list[MCPToolset] | None = None,
        cache: ModelCacheControl | None = None,
        tool_search: ToolSearchControl | None = None,
        dynamic_loading: DynamicToolLoadingSpec | None = None,
        data_sources: list[DataSourceRef] | None = None,
        workspace: Any | None = None,
        workspace_provider: Any | None = None,
        workspace_policy: Any = None,
        workspace_prefix: str = "workspace",
        policy: Any = None,
    ) -> ResolvedRunSpec:
        """Resolve a run without executing it and return the prompt/tool plan."""
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        capability_profile = get_model_capability_profile(adapter, model_name)
        effective_output_schema = output_schema
        effective_output_strategy = output_strategy
        if output_spec is not None:
            effective_output_schema = build_output_schema(output_spec)
            effective_output_strategy = output_strategy or output_spec.strategy
        if effective_output_schema is not None and effective_output_strategy is not None:
            effective_output_strategy = resolve_output_strategy(
                profile=capability_profile,
                requested=effective_output_strategy,
                fallback=output_spec.fallback if output_spec is not None else "posthoc_parse",
            )
            if effective_output_strategy == "posthoc_parse":
                effective_output_schema = None

        effective_tool_session = tool_session
        effective_tools = list(tools or [])
        effective_hosted_tools = list(hosted_tools or [])
        resolved_mcp_toolsets: list[ResolvedMCPToolset] = []
        active_mcp_connectors: list[MCPConnector] = []
        opened_workspace: tuple[Any, Any, bool] | None = None
        try:
            if workspace is not None:
                effective_tool_session = (
                    ToolSession.from_registry(tool_session.registry)
                    if tool_session is not None
                    else self.tools.session()
                )
                workspace_provider_obj, workspace_ref, should_close = await self._open_runtime_workspace(
                    workspace,
                    provider=workspace_provider,
                    policy=workspace_policy or policy,
                )
                opened_workspace = (workspace_provider_obj, workspace_ref, should_close)
                registered_workspace_tools = effective_tool_session.register_workspace(
                    workspace_provider_obj,
                    workspace_ref,
                    prefix=workspace_prefix,
                )
                effective_tools.extend(tool.name for tool in registered_workspace_tools)

            for mcp_toolset in toolsets or []:
                route = resolve_mcp_route(
                    provider_ref,
                    mcp_toolset,
                    capability_profile,
                    policy=policy,
                )
                if route == "provider_native":
                    effective_hosted_tools.append(to_remote_mcp(mcp_toolset))
                    resolved_mcp_toolsets.append(
                        _resolved_mcp_toolset(mcp_toolset, route="provider_native")
                    )
                    continue
                effective_tool_session = (
                    ToolSession.from_registry(effective_tool_session.registry)
                    if effective_tool_session is not None
                    else self.tools.session()
                )
                connector = MCPConnector(
                    [mcp_toolset.server_for_local_dispatch()],
                    policy=None,
                )
                registered_mcp_tools = await connector.register_runtime_tools(
                    effective_tool_session.registry
                )
                effective_tools.extend(tool.name for tool in registered_mcp_tools)
                resolved_mcp_toolsets.append(
                    _resolved_mcp_toolset(
                        mcp_toolset,
                        route="local",
                        allowed_tools=[tool.name for tool in registered_mcp_tools],
                    )
                )
                active_mcp_connectors.append(connector)

            tool_definitions = self._tool_payload(
                effective_tools,
                tool_session=effective_tool_session,
            )
            if (
                effective_output_schema is not None
                and effective_output_strategy == "finalizer_tool"
            ):
                tool_definitions.append(_finalizer_tool_payload(effective_output_schema))

            prompt_spec = _resolve_prompt_spec(
                prompt,
                prompt_mode=prompt_mode,
                channel=channel,
            )
            plan = self._resolved_run_spec(
                provider_ref=provider_ref,
                model_name=model_name,
                capability_profile=capability_profile,
                input=input,
                instructions=instructions,
                prompt_spec=prompt_spec,
                tool_definitions=tool_definitions,
                effective_hosted_tools=effective_hosted_tools,
                mcp_toolsets=resolved_mcp_toolsets,
                workspace=opened_workspace[1] if opened_workspace is not None else None,
                output_spec=output_spec,
                output_strategy=effective_output_strategy,
                cache=cache,
                dynamic_loading=dynamic_loading
                or _dynamic_loading_from_controls(
                    tool_search=tool_search,
                    hosted_tools=effective_hosted_tools,
                    mcp_toolsets=resolved_mcp_toolsets,
                ),
                data_sources=data_sources,
                tool_session=effective_tool_session,
            )
            plan.prompt = self.prompt_composer.build(plan, prompt_spec)
            return plan
        finally:
            if opened_workspace is not None:
                workspace_provider_obj, workspace_ref, should_close = opened_workspace
                if should_close:
                    await workspace_provider_obj.close(workspace_ref)
            for connector in active_mcp_connectors:
                await connector.stop()

    async def stream(
        self,
        *,
        provider: str,
        input: str,
        model: str | None = None,
        tools: list[str] | None = None,
        tool_session: ToolSession | None = None,
        tool_execution_context: dict[str, Any] | None = None,
        tool_max_concurrent: int | None = None,
        tool_timeout: float | None = None,
        approval_policy: Any = None,
        policy: Any = None,
        max_iterations: int = 8,
        mock_tools: bool = False,
        provider_state: ProviderState | None = None,
        instructions: str | None = None,
        prompt: PromptSpec | None = None,
        prompt_mode: PromptMode | None = None,
        channel: str | None = None,
        output_schema: OutputSchema | None = None,
        output_strategy: OutputStrategy | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        toolsets: list[MCPToolset] | None = None,
        hosted_tool_handlers: HostedToolHandlers | None = None,
        cache: ModelCacheControl | None = None,
        tool_search: ToolSearchControl | None = None,
        dynamic_loading: DynamicToolLoadingSpec | None = None,
        compaction: CompactionControl | None = None,
        modalities: list[str] | None = None,
        data_sources: list[DataSourceRef] | None = None,
        workspace: Any | None = None,
        workspace_provider: Any | None = None,
        workspace_policy: Any = None,
        workspace_prefix: str = "workspace",
        workspace_preserve: bool = False,
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
        adapter = self.registry.get_model(provider_ref.provider_key)
        capability_profile = get_model_capability_profile(adapter, model_name)

        effective_tool_session = tool_session
        effective_tools = list(tools or [])
        effective_hosted_tools = list(hosted_tools or [])
        resolved_mcp_toolsets: list[ResolvedMCPToolset] = []
        active_mcp_connectors: list[MCPConnector] = []
        opened_workspace: tuple[Any, Any, bool] | None = None
        if workspace is not None:
            effective_tool_session = (
                ToolSession.from_registry(tool_session.registry)
                if tool_session is not None
                else self.tools.session()
            )
            workspace_provider_obj, workspace_ref, should_close = await self._open_runtime_workspace(
                workspace,
                provider=workspace_provider,
                policy=workspace_policy or policy,
            )
            opened_workspace = (workspace_provider_obj, workspace_ref, should_close)
            registered_workspace_tools = effective_tool_session.register_workspace(
                workspace_provider_obj,
                workspace_ref,
                prefix=workspace_prefix,
            )
            effective_tools.extend(tool.name for tool in registered_workspace_tools)

        for mcp_toolset in toolsets or []:
            route = resolve_mcp_route(
                provider_ref,
                mcp_toolset,
                capability_profile,
                policy=policy,
            )
            if route == "provider_native":
                effective_hosted_tools.append(to_remote_mcp(mcp_toolset))
                resolved_mcp_toolsets.append(
                    _resolved_mcp_toolset(mcp_toolset, route="provider_native")
                )
                continue
            effective_tool_session = (
                ToolSession.from_registry(effective_tool_session.registry)
                if effective_tool_session is not None
                else self.tools.session()
            )
            connector = MCPConnector(
                [mcp_toolset.server_for_local_dispatch()],
                policy=None,
            )
            registered_mcp_tools = await connector.register_runtime_tools(
                effective_tool_session.registry
            )
            effective_tools.extend(tool.name for tool in registered_mcp_tools)
            resolved_mcp_toolsets.append(
                _resolved_mcp_toolset(
                    mcp_toolset,
                    route="local",
                    allowed_tools=[tool.name for tool in registered_mcp_tools],
                )
            )
            active_mcp_connectors.append(connector)

        tool_definitions = self._tool_payload(effective_tools, tool_session=effective_tool_session)
        if output_schema is not None and output_strategy == "finalizer_tool":
            tool_definitions.append(_finalizer_tool_payload(output_schema))
        tool_runtime = self._tool_runtime_for(
            tool_execution_context,
            tool_session=effective_tool_session,
            max_concurrent=tool_max_concurrent,
            timeout=tool_timeout,
        )
        prompt_spec = _resolve_prompt_spec(prompt, prompt_mode=prompt_mode, channel=channel)
        run_plan = self._resolved_run_spec(
            provider_ref=provider_ref,
            model_name=model_name,
            capability_profile=capability_profile,
            input=input,
            instructions=instructions,
            prompt_spec=prompt_spec,
            tool_definitions=tool_definitions,
            effective_hosted_tools=effective_hosted_tools,
            mcp_toolsets=resolved_mcp_toolsets,
            workspace=opened_workspace[1] if opened_workspace is not None else None,
            output_strategy=output_strategy,
            cache=cache,
            dynamic_loading=dynamic_loading
            or _dynamic_loading_from_controls(
                tool_search=tool_search,
                hosted_tools=effective_hosted_tools,
                mcp_toolsets=resolved_mcp_toolsets,
            ),
            data_sources=data_sources,
            tool_session=effective_tool_session,
        )
        try:
            prompt_bundle = self.prompt_composer.build(run_plan, prompt_spec)
        except Exception:
            if opened_workspace is not None and not workspace_preserve:
                workspace_provider_obj, workspace_ref, should_close = opened_workspace
                if should_close:
                    await workspace_provider_obj.close(workspace_ref)
            for connector in active_mcp_connectors:
                await connector.stop()
            raise
        run_plan.prompt = prompt_bundle
        tool_definitions = [dict(tool.definition) for tool in run_plan.tools]
        effective_hosted_tools = [tool.spec for tool in run_plan.hosted_tools]

        run_id = f"run_{uuid4().hex}"
        trace_context = TraceContext.for_run(run_id)

        async def stream_factory(
            *, input: str | list[Any], provider_state: ProviderState | None
        ) -> AsyncIterator[AgentEvent]:
            async for event in self.models.stream(
                provider=provider,
                model=model_name,
                input=input,
                provider_state=provider_state,
                tools=tool_definitions,
                hosted_tools=effective_hosted_tools,
                output_schema=output_schema,
                output_strategy=output_strategy,
                instructions=prompt_bundle.instructions or None,
                cache=cache,
                tool_search=tool_search,
                compaction=compaction,
                modalities=modalities,
                run_id=run_id,
                trace_id=trace_context.trace_id,
                parent_span_id=trace_context.root_span_id,
                **kwargs,
            ):
                yield event

        loop = AgentLoop(
            stream_factory=stream_factory,
            tools=(
                tool_runtime
                if effective_tools
                or (output_schema is not None and output_strategy == "finalizer_tool")
                else None
            ),
            hosted_tools=effective_hosted_tools,
            hosted_tool_handlers=hosted_tool_handlers or HostedToolHandlers(),
            approval_policy=approval_policy,
            policy=policy,
            max_iterations=max_iterations,
            finalizer_tool_name=(
                _FINALIZER_TOOL_NAME
                if output_schema is not None and output_strategy == "finalizer_tool"
                else None
            ),
        )
        self._active_loops[run_id] = loop
        try:
            sequence = 0
            for event in _prompt_events(run_plan, prompt_bundle):
                stamped = trace_context.stamp(
                    event,
                    run_id=run_id,
                    sequence=sequence,
                    preserve_sequence=False,
                )
                sequence += 1
                await self.event_store.append(stamped)
                yield stamped
            if opened_workspace is not None:
                workspace_provider_obj, _, _ = opened_workspace
                for event in workspace_provider_obj.drain_events():
                    stamped = trace_context.stamp(
                        event,
                        run_id=run_id,
                        sequence=sequence,
                        preserve_sequence=False,
                    )
                    sequence += 1
                    await self.event_store.append(stamped)
                    yield stamped

            async for event in loop.run(
                input=input,
                provider_state=provider_state,
                provider_id=provider_ref.provider_key,
                mock_tools=mock_tools,
            ):
                stamped = trace_context.stamp(
                    event,
                    run_id=run_id,
                    sequence=sequence,
                    preserve_sequence=False,
                )
                sequence += 1
                await self.event_store.append(stamped)
                yield stamped
            if opened_workspace is not None and not workspace_preserve:
                workspace_provider_obj, workspace_ref, should_close = opened_workspace
                if should_close:
                    await workspace_provider_obj.close(workspace_ref)
                    for event in workspace_provider_obj.drain_events():
                        stamped = trace_context.stamp(
                            event,
                            run_id=run_id,
                            sequence=sequence,
                            preserve_sequence=False,
                        )
                        sequence += 1
                        await self.event_store.append(stamped)
                        yield stamped
        finally:
            self._active_loops.pop(run_id, None)
            for connector in active_mcp_connectors:
                await connector.stop()

    async def run(
        self,
        *,
        provider: str,
        input: str,
        model: str | None = None,
        tools: list[str] | None = None,
        tool_session: ToolSession | None = None,
        tool_execution_context: dict[str, Any] | None = None,
        tool_max_concurrent: int | None = None,
        tool_timeout: float | None = None,
        approval_policy: Any = None,
        policy: Any = None,
        max_iterations: int = 8,
        mock_tools: bool = False,
        output_type: type[T] | None = None,
        output_spec: OutputSpec | None = None,
        provider_state: ProviderState | None = None,
        instructions: str | None = None,
        prompt: PromptSpec | None = None,
        prompt_mode: PromptMode | None = None,
        channel: str | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        toolsets: list[MCPToolset] | None = None,
        hosted_tool_handlers: HostedToolHandlers | None = None,
        cache: ModelCacheControl | None = None,
        tool_search: ToolSearchControl | None = None,
        dynamic_loading: DynamicToolLoadingSpec | None = None,
        compaction: CompactionControl | None = None,
        modalities: list[str] | None = None,
        data_sources: list[DataSourceRef] | None = None,
        workspace: Any | None = None,
        workspace_provider: Any | None = None,
        workspace_policy: Any = None,
        workspace_prefix: str = "workspace",
        workspace_preserve: bool = False,
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
        output_schema = build_output_schema(spec)
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        effective_strategy: OutputStrategy | None = spec.strategy
        initial_provider_native_fallback: str | None = None
        if output_schema is not None:
            profile = get_model_capability_profile(adapter, model_name)
            resolved_strategy = resolve_output_strategy(
                profile=profile,
                requested=spec.strategy,
                fallback=spec.fallback,
            )
            if resolved_strategy != spec.strategy:
                initial_provider_native_fallback = resolved_strategy
            effective_strategy = resolved_strategy
            if effective_strategy == "posthoc_parse":
                output_schema = None
        attempts_allowed = (
            1 + spec.max_validation_retries
            if effective_strategy == "posthoc_parse_with_retry"
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
        finalizer_output: dict[str, Any] | None = None
        last_error: OutputValidationError | None = None
        current_input = input
        provider_native_fallback: str | None = initial_provider_native_fallback

        for attempt in range(attempts_allowed):
            text_parts: list[str] = []
            while True:
                try:
                    async for event in self.stream(
                        provider=provider,
                        input=current_input,
                        model=model,
                        tools=tools,
                        tool_session=tool_session,
                        tool_execution_context=tool_execution_context,
                        tool_max_concurrent=tool_max_concurrent,
                        tool_timeout=tool_timeout,
                        approval_policy=approval_policy,
                        policy=policy,
                        max_iterations=max_iterations,
                        mock_tools=mock_tools,
                        provider_state=captured_state,
                        instructions=instructions,
                        prompt=prompt,
                        prompt_mode=prompt_mode,
                        channel=channel,
                        hosted_tools=hosted_tools,
                        toolsets=toolsets,
                        hosted_tool_handlers=hosted_tool_handlers,
                        output_schema=output_schema,
                        output_strategy=effective_strategy if output_schema is not None else None,
                        cache=cache,
                        tool_search=tool_search,
                        dynamic_loading=dynamic_loading,
                        compaction=compaction,
                        modalities=modalities,
                        data_sources=data_sources,
                        workspace=workspace,
                        workspace_provider=workspace_provider,
                        workspace_policy=workspace_policy,
                        workspace_prefix=workspace_prefix,
                        workspace_preserve=workspace_preserve,
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
                        elif event.type == EventTypes.ARTIFACT_CREATED:
                            artifact = event.data.get("artifact")
                            if isinstance(artifact, Artifact):
                                artifacts.append(artifact)
                        else:
                            artifact = event.data.get("artifact")
                            if isinstance(artifact, Artifact):
                                artifacts.append(artifact)
                        item = event.data.get("item")
                        if isinstance(item, RunItem):
                            items.append(item)
                        event_metadata = event.data.get("metadata")
                        if (
                            event.type == EventTypes.TOOL_CALL_COMPLETED
                            and isinstance(event_metadata, dict)
                            and event_metadata.get("purpose") == "final_output"
                        ):
                            arguments = event.data.get("arguments")
                            if isinstance(arguments, dict):
                                finalizer_output = arguments
                        maybe_state = event.data.get("provider_state")
                        if isinstance(maybe_state, ProviderState):
                            captured_state = maybe_state
                        if event.type == EventTypes.MODEL_COMPLETED:
                            usage = add_usage(usage, _event_usage(event))
                            completed_provider = event.provider or completed_provider
                            model_value = event.data.get("model")
                            if isinstance(model_value, str):
                                completed_model = model_value
                    break
                except ProviderExecutionError:
                    if not _can_fallback_provider_native(
                        effective_strategy=effective_strategy,
                        output_schema=output_schema,
                        fallback=spec.fallback,
                        already_used=provider_native_fallback is not None,
                    ):
                        raise
                    provider_native_fallback = spec.fallback
                    effective_strategy = cast(OutputStrategy, spec.fallback)
                    if effective_strategy == "posthoc_parse":
                        output_schema = None
                    text_parts = []
                    continue

            last_text = (
                json.dumps(finalizer_output)
                if finalizer_output is not None
                else "".join(text_parts)
            )
            try:
                output = _validate_output(
                    last_text,
                    spec.schema,
                    allow_dict_schema=effective_strategy in {"provider_native", "finalizer_tool"},
                )
            except OutputValidationError as exc:
                last_error = exc
                if attempt + 1 >= attempts_allowed:
                    raise
                current_input = _build_repair_prompt(
                    previous_text=last_text, error=exc, attempt=attempt + 1
                )
                continue

            usage = add_usage(usage, _tool_usage_from_events(events))
            metadata: dict[str, Any] = {"validation_attempts": attempt + 1}
            if provider_native_fallback is not None:
                metadata["provider_native_fallback"] = provider_native_fallback
            usage_dict = usage_to_dict(usage)
            if usage_dict is not None:
                metadata["usage"] = usage_dict
            provider_usage = usage_provider_details(usage)
            if provider_usage is not None:
                metadata["usage_provider_details"] = provider_usage
            if usage is not None and completed_provider is not None and completed_model is not None:
                cost = self.model_catalog.estimate_cost(
                    provider=completed_provider, model=completed_model, usage=usage
                )
                if cost is not None:
                    metadata["cost"] = cost
            cache_metadata = await _provider_cache_metadata(
                provider=completed_provider,
                model=completed_model,
                cache=cache,
                usage=usage,
                provider_cache_store=self.provider_cache_store,
            )
            if cache_metadata:
                metadata["cache"] = cache_metadata
            mcp_metadata = _mcp_metadata_from_events(events)
            if mcp_metadata:
                metadata["mcp"] = mcp_metadata
            hosted_metadata = _hosted_tool_metadata_from_events(events)
            if hosted_metadata:
                metadata["hosted_tools"] = hosted_metadata
            workspace_metadata = _workspace_metadata_from_events(events)
            if workspace_metadata:
                metadata["workspaces"] = workspace_metadata
            prompt_metadata = _prompt_metadata_from_events(events)
            if prompt_metadata:
                metadata["prompt"] = prompt_metadata
            trace_metadata = trace_metadata_from_events(events, metadata=metadata)
            if trace_metadata["spans"]:
                metadata["trace"] = trace_metadata
            _attach_accounting_metadata(metadata)
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

    def _resolved_run_spec(
        self,
        *,
        provider_ref: ProviderRef,
        model_name: str,
        capability_profile: ModelCapabilityProfile,
        input: object,
        instructions: str | None,
        prompt_spec: PromptSpec,
        tool_definitions: list[dict[str, Any]],
        effective_hosted_tools: list[HostedToolSpec],
        mcp_toolsets: list[ResolvedMCPToolset],
        workspace: Any | None,
        output_spec: OutputSpec | None = None,
        output_strategy: OutputStrategy | str | None = None,
        cache: ModelCacheControl | None = None,
        dynamic_loading: DynamicToolLoadingSpec | None = None,
        data_sources: list[DataSourceRef] | None = None,
        tool_session: ToolSession | None = None,
    ) -> ResolvedRunSpec:
        registry = tool_session.registry if tool_session is not None else self.tools.registry
        available_tool_ids = [tool.name for tool in registry.all_tools()]
        for tool_name in _tool_names(tool_definitions):
            if tool_name not in available_tool_ids:
                available_tool_ids.append(tool_name)
        return ResolvedRunSpec(
            provider=provider_ref.provider_key,
            model=model_name,
            provider_profile=capability_profile,
            input=input,
            base_instructions=instructions,
            channel=prompt_spec.channel,
            tools=_resolved_tools(tool_definitions, registry=registry),
            hosted_tools=resolved_hosted_tools(effective_hosted_tools),
            mcp_toolsets=mcp_toolsets,
            workspace=workspace,
            output_spec=output_spec,
            output_strategy=output_strategy,
            dynamic_loading=dynamic_loading,
            cache=cache,
            data_sources=list(data_sources or []),
            available_tool_ids=available_tool_ids,
            metadata={
                "prompt_mode": prompt_spec.mode,
                "prompt_parity": prompt_spec.parity,
            },
        )

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
        self,
        context: dict[str, Any] | None,
        *,
        tool_session: ToolSession | None = None,
        max_concurrent: int | None = None,
        timeout: float | None = None,
    ) -> ToolRuntime:
        if tool_session is not None:
            return tool_session.runtime(
                context=context,
                max_concurrent=max_concurrent,
                timeout=timeout,
            )
        if context is None and max_concurrent is None and timeout is None:
            return self.tools.default_runtime
        return ToolRuntime(
            self.tools.registry,
            context=context or {},
            max_concurrent=max_concurrent,
            timeout=timeout,
        )

    async def _open_runtime_workspace(
        self,
        workspace: Any,
        *,
        provider: Any | None,
        policy: Any,
    ) -> tuple[Any, Any, bool]:
        workspace_ref, workspace_provider, opened = await self.workspaces.resolve(
            workspace,
            provider=provider,
            policy=policy,
        )
        if workspace_ref is None or workspace_provider is None:
            raise ConfigurationError("workspace could not be resolved.")
        return workspace_provider, workspace_ref, opened


def _resolve_output_spec(
    output_type: type[Any] | None, output_spec: OutputSpec | None
) -> OutputSpec:
    if output_type is not None and output_spec is not None:
        raise ValueError("Pass either output_type or output_spec, not both.")
    if output_spec is not None:
        return output_spec
    return OutputSpec(schema=output_type, strategy="posthoc_parse")


def _resolve_prompt_spec(
    prompt: PromptSpec | None,
    *,
    prompt_mode: PromptMode | None,
    channel: str | None,
) -> PromptSpec:
    prompt_spec = prompt or PromptSpec()
    if prompt_mode is not None:
        prompt_spec = replace(prompt_spec, mode=prompt_mode)
    if channel is not None:
        prompt_spec = replace(prompt_spec, channel=channel)
    return prompt_spec


def _resolved_tools(
    tool_definitions: list[dict[str, Any]],
    *,
    registry: ToolRegistry,
) -> list[ResolvedTool]:
    registered = {tool.name: tool for tool in registry.all_tools()}
    resolved: list[ResolvedTool] = []
    for payload in tool_definitions:
        name = payload.get("name")
        if not isinstance(name, str):
            continue
        definition = registered.get(name)
        metadata = payload.get("metadata")
        payload_metadata = metadata if isinstance(metadata, dict) else {}
        resolved.append(
            ResolvedTool(
                name=name,
                definition=payload,
                tags=frozenset(definition.tags if definition is not None else ()),
                category=definition.category if definition is not None else None,
                metadata={
                    **dict(definition.metadata if definition is not None else {}),
                    **payload_metadata,
                },
                prompt_fragments=tuple(
                    definition.prompt_fragments if definition is not None else ()
                ),
                hidden=payload_metadata.get("agent_runtime_hidden") is True,
            )
        )
    return resolved


def _resolved_mcp_toolset(
    toolset: MCPToolset,
    *,
    route: str,
    allowed_tools: list[str] | None = None,
) -> ResolvedMCPToolset:
    configured_allowed = (
        toolset.allowed_tools
        if toolset.allowed_tools is not None
        else toolset.server.allowed_tools
    )
    tools = allowed_tools if allowed_tools is not None else configured_allowed
    return ResolvedMCPToolset(
        server_label=toolset.server.name,
        route=route,
        allowed_tools=tuple(tools or ()),
        metadata={
            "mode": toolset.mode,
            "transport": toolset.server.transport,
            "description": toolset.description,
        },
    )


def _dynamic_loading_from_controls(
    *,
    tool_search: ToolSearchControl | None,
    hosted_tools: list[HostedToolSpec],
    mcp_toolsets: list[ResolvedMCPToolset],
) -> DynamicToolLoadingSpec | None:
    hosted_kinds = {hosted_tool_kind(tool) for tool in hosted_tools}
    if tool_search is not None or "tool_search" in hosted_kinds:
        return DynamicToolLoadingSpec(mode="tool_search")
    if any(toolset.route == "provider_native" for toolset in mcp_toolsets):
        return DynamicToolLoadingSpec(mode="provider_native_mcp")
    return None


def _prompt_events(plan: ResolvedRunSpec, bundle: PromptBundle) -> list[AgentEvent]:
    events = [
        AgentEvent(
            type=EventTypes.PROMPT_PLAN_CREATED,
            provider=plan.provider,
            data={
                "provider": plan.provider,
                "model": plan.model,
                "mode": bundle.metadata.get("mode"),
                "channel": bundle.metadata.get("channel"),
                "effective_tool_ids": list(plan.effective_tool_ids),
                "hosted_tool_kinds": list(plan.hosted_tool_kinds),
                "mcp_servers": list(plan.mcp_servers),
                "output_strategy": plan.output_strategy,
                "dynamic_loading_mode": (
                    plan.dynamic_loading.mode if plan.dynamic_loading is not None else None
                ),
            },
        )
    ]
    events.extend(
        AgentEvent(
            type=EventTypes.PROMPT_FRAGMENT_SELECTED,
            provider=plan.provider,
            data={
                "fragment_id": item.fragment.id,
                "source": item.fragment.source,
                "priority": item.fragment.priority,
                "cacheable": item.fragment.cacheable,
                "placement": item.fragment.placement,
                "matched_on": dict(item.matched_on),
            },
        )
        for item in bundle.selected_fragments
    )
    events.extend(
        AgentEvent(
            type=EventTypes.PROMPT_FRAGMENT_SKIPPED,
            provider=plan.provider,
            data={
                "fragment_id": item.fragment.id,
                "source": item.fragment.source,
                "reason": item.reason,
                "details": dict(item.details),
            },
        )
        for item in bundle.skipped_fragments
    )
    events.extend(
        AgentEvent(
            type=EventTypes.PROMPT_CACHE_SECTION_CREATED,
            provider=plan.provider,
            data={
                "section_id": section.id,
                "cacheable": section.cacheable,
                "content_length": len(section.content),
            },
        )
        for section in bundle.cache_sections
    )
    events.append(
        AgentEvent(
            type=EventTypes.PROMPT_PARITY_CHECKED,
            provider=plan.provider,
            data={
                "status": bundle.metadata.get("parity"),
                "issues": [issue.to_dict() for issue in bundle.parity_issues],
                "parity_fingerprint": bundle.parity_fingerprint,
            },
        )
    )
    events.append(
        AgentEvent(
            type=EventTypes.PROMPT_BUNDLE_CREATED,
            provider=plan.provider,
            data={
                "section_ids": [section.id for section in bundle.sections],
                "fragment_ids": [item.fragment.id for item in bundle.selected_fragments],
                "effective_tool_ids": list(bundle.effective_tool_ids),
                "prompt_fingerprint": bundle.prompt_fingerprint,
                "parity_fingerprint": bundle.parity_fingerprint,
                "metadata": dict(bundle.metadata),
            },
        )
    )
    return events


def _agent_task_spec(
    task: str | TaskSpec,
    *,
    model: str | None,
    workspace: Any | None,
    inputs: list[ArtifactRef] | None,
    metadata: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> TaskSpec:
    if isinstance(task, TaskSpec):
        return replace(
            task,
            model=model if model is not None else task.model,
            workspace=workspace if workspace is not None else task.workspace,
            inputs=list(inputs) if inputs is not None else list(task.inputs),
            metadata={**task.metadata, **dict(metadata or {})},
            extra={**task.extra, **dict(extra or {})},
        )
    return TaskSpec(
        prompt=task,
        model=model,
        workspace=workspace,
        inputs=list(inputs or []),
        metadata=dict(metadata or {}),
        extra=dict(extra or {}),
    )


def _workspace_ref_metadata(workspace: Any) -> dict[str, Any]:
    if workspace is None:
        return {}
    state = getattr(workspace, "session_state", None)
    metadata = {
        "id": getattr(workspace, "id", None),
        "kind": getattr(workspace, "kind", None),
        "provider": getattr(workspace, "provider", None),
        "root": getattr(workspace, "root", None),
        "name": getattr(workspace, "name", None),
        "provider_workspace_id": getattr(workspace, "provider_workspace_id", None),
        "provider_session_id": getattr(workspace, "provider_session_id", None),
        "metadata": dict(getattr(workspace, "metadata", {}) or {}),
    }
    if state is not None:
        from agent_runtime.workspaces.serialization import workspace_state_to_dict

        metadata["session_state"] = workspace_state_to_dict(state)
    return metadata


def _drain_workspace_events(provider: Any, workspace: Any) -> list[AgentEvent]:
    drain = getattr(provider, "drain_events", None)
    if not callable(drain):
        return []
    return [_agent_event_with_workspace(event, workspace) for event in drain()]


def _agent_event_with_workspace(event: AgentEvent, workspace: Any) -> AgentEvent:
    workspace_id = getattr(workspace, "id", None)
    workspace_kind = getattr(workspace, "kind", None)
    workspace_provider = getattr(workspace, "provider", None)
    if not isinstance(workspace_id, str):
        return event
    event_type = _canonical_workspace_event_type(event.type)
    data = dict(event.data)
    should_attach = (
        event_type.startswith("workspace.")
        or event_type == EventTypes.ARTIFACT_CREATED
        or event_type.startswith("approval.")
    )
    if should_attach:
        data.setdefault("workspace_id", workspace_id)
        data.setdefault("workspace_kind", workspace_kind)
        data.setdefault("workspace_provider", workspace_provider)
        artifact = data.get("artifact")
        if isinstance(artifact, Artifact):
            data["artifact"] = replace(
                artifact,
                metadata={
                    "workspace_id": workspace_id,
                    "workspace_kind": workspace_kind,
                    "workspace_provider": workspace_provider,
                    **dict(artifact.metadata),
                },
            )
        elif isinstance(artifact, dict):
            artifact_data = dict(artifact)
            artifact_data["metadata"] = {
                "workspace_id": workspace_id,
                "workspace_kind": workspace_kind,
                "workspace_provider": workspace_provider,
                **dict(artifact_data.get("metadata") or {}),
            }
            data["artifact"] = artifact_data
    return replace(event, type=event_type, data=data)


def _canonical_workspace_event_type(event_type: str) -> str:
    aliases = {
        "file_read": EventTypes.WORKSPACE_FILE_READ,
        "file.changed": EventTypes.WORKSPACE_FILE_CHANGED,
        "file_changed": EventTypes.WORKSPACE_FILE_CHANGED,
        "patch": EventTypes.WORKSPACE_PATCH_CREATED,
        "patch_created": EventTypes.WORKSPACE_PATCH_CREATED,
        "command_started": EventTypes.WORKSPACE_COMMAND_STARTED,
        "command_output": EventTypes.WORKSPACE_COMMAND_OUTPUT,
        "command_completed": EventTypes.WORKSPACE_COMMAND_COMPLETED,
        "test_started": EventTypes.WORKSPACE_TEST_STARTED,
        "test_completed": EventTypes.WORKSPACE_TEST_COMPLETED,
        "snapshot_created": EventTypes.WORKSPACE_SNAPSHOT_CREATED,
        "artifact": EventTypes.ARTIFACT_CREATED,
        "approval_required": EventTypes.APPROVAL_REQUESTED,
    }
    return aliases.get(event_type, event_type)


def _agent_event_text_delta(event: AgentEvent) -> str | None:
    if event.type != EventTypes.MODEL_TEXT_DELTA:
        return None
    return _first_text(event.data, "delta", "message", "text")


def _agent_event_text(event: AgentEvent) -> str | None:
    return _first_text(event.data, "output", "message", "text", "result")


def _first_text(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            return value
    return None


def _artifact_from_event(event: AgentEvent) -> Artifact | None:
    artifact = event.data.get("artifact")
    if isinstance(artifact, Artifact):
        return artifact
    if not isinstance(artifact, dict):
        return None
    metadata = dict(artifact.get("metadata") or {})
    metadata.setdefault("event_id", event.id)
    if event.provider is not None:
        metadata.setdefault("provider", event.provider)
    artifact_kwargs: dict[str, Any] = {
        "type": str(artifact.get("type", "provider_ref")),
        "name": str(
            artifact.get("name")
            or artifact.get("path")
            or artifact.get("id")
            or "artifact"
        ),
        "data": artifact.get("data"),
        "uri": artifact.get("uri"),
        "metadata": metadata,
    }
    if artifact.get("id") is not None:
        artifact_kwargs["id"] = str(artifact["id"])
    return Artifact(**artifact_kwargs)


def _append_artifact_unique(
    artifacts: list[Artifact],
    artifact_ids: set[str],
    artifact: Artifact,
) -> None:
    if artifact.id in artifact_ids:
        return
    artifact_ids.add(artifact.id)
    artifacts.append(artifact)


def _agent_session_result_status(
    session: AgentSession,
    events: list[AgentEvent],
) -> AgentSessionResultStatus:
    for event in reversed(events):
        if _agent_event_is_timeout(event):
            return "timeout"
        if event.type == EventTypes.SESSION_COMPLETED:
            return "completed"
        if event.type == EventTypes.SESSION_FAILED:
            return "failed"
        if event.type == EventTypes.SESSION_CANCELLED:
            return "cancelled"

    if session.status == "completed":
        return "completed"
    if session.status == "failed":
        return "failed"
    if session.status == "cancelled":
        return "cancelled"
    if session.status == "waiting":
        return "waiting_for_approval"
    return "failed"


def _agent_event_is_timeout(event: AgentEvent) -> bool:
    if event.type.endswith(".timeout") or event.type.endswith(".timed_out"):
        return True
    status = event.data.get("status")
    reason = event.data.get("reason")
    error = event.data.get("error")
    return status == "timeout" or reason == "timeout" or error == "timeout"


def _provider_state_from_session(
    session: AgentSession,
    *,
    events: list[AgentEvent],
) -> ProviderState:
    metadata = {key: value for key, value in session.metadata.items() if key != "raw"}
    continuation: dict[str, Any] = {
        "session_id": session.id,
        "status": session.status,
    }
    if session.agent_id is not None:
        continuation["agent_id"] = session.agent_id
    if events:
        metadata_last_event_id = metadata.get("last_event_id")
        last_event = (
            next(
                (event for event in reversed(events) if event.id == metadata_last_event_id),
                None,
            )
            if isinstance(metadata_last_event_id, str)
            else None
        ) or events[-1]
        continuation["last_event_id"] = last_event.id
        continuation["last_sequence"] = last_event.sequence
        continuation["run_id"] = last_event.run_id
    if metadata:
        continuation["metadata"] = metadata
    return ProviderState(
        provider=session.provider,
        conversation_id=session.id,
        continuation=continuation,
    )


def _can_fallback_provider_native(
    *,
    effective_strategy: OutputStrategy | None,
    output_schema: OutputSchema | None,
    fallback: str,
    already_used: bool,
) -> bool:
    return (
        effective_strategy == "provider_native"
        and output_schema is not None
        and fallback in {"posthoc_parse", "finalizer_tool"}
        and not already_used
    )


def _mcp_metadata_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    tool_lists: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    for event in events:
        if event.type == EventTypes.MCP_LIST_TOOLS_COMPLETED:
            tools = event.data.get("tools")
            tool_names = _tool_names(tools)
            tool_lists.append(
                {
                    "server_label": event.data.get("server_label"),
                    "tool_count": len(tool_names),
                    "tool_names": tool_names,
                    "item_id": event.item_id,
                }
            )
        elif event.type == EventTypes.MCP_CALL_COMPLETED:
            calls.append(
                {
                    "server_label": event.data.get("server_label"),
                    "name": event.data.get("name"),
                    "item_id": event.item_id,
                    "failed": event.data.get("error") is not None
                    or event.data.get("is_error") is True,
                }
            )
        elif event.type == EventTypes.MCP_APPROVAL_REQUIRED:
            approvals.append(
                {
                    "server_label": event.data.get("server_label"),
                    "name": event.data.get("name"),
                    "item_id": event.item_id,
                }
            )
    if not (tool_lists or calls or approvals):
        return {}
    metadata: dict[str, Any] = {
        "tool_lists": tool_lists,
        "calls": calls,
        "approval_requests": approvals,
    }
    if tool_lists:
        metadata["tool_list_cacheable"] = True
        metadata["context_item_ids"] = [
            item["item_id"] for item in tool_lists if item.get("item_id") is not None
        ]
    return metadata


def _hosted_tool_metadata_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    for event in events:
        item = event.data.get("item")
        item_type = event.data.get("hosted_tool_type")
        if item_type is None and isinstance(item, RunItem):
            item_type = item.data.get("hosted_tool_type")
        if not isinstance(item_type, str):
            continue
        summary = {
            "type": item_type,
            "item_type": event.data.get("item_type"),
            "item_id": event.item_id,
            "status": event.data.get("status")
            or (item.status if isinstance(item, RunItem) else None),
        }
        if "query" in event.data:
            summary["query"] = event.data["query"]
        if "call_id" in event.data:
            summary["call_id"] = event.data["call_id"]
        if "results" in event.data:
            summary["result_count"] = len(event.data["results"]) if isinstance(
                event.data["results"], list
            ) else None
        if event.type == EventTypes.MODEL_ITEM_COMPLETED:
            completed.append(summary)
        else:
            calls.append(summary)
    if not (calls or completed):
        return {}
    return {
        "calls": calls,
        "completed": completed,
        "types": sorted(
            {
                item["type"]
                for item in [*calls, *completed]
                if isinstance(item.get("type"), str)
            }
        ),
    }


def _workspace_metadata_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    workspaces: dict[str, dict[str, Any]] = {}
    for event in events:
        data = event.data
        metadata = data.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        workspace_id = data.get("workspace_id") or metadata_dict.get("workspace_id")
        if not isinstance(workspace_id, str):
            artifact = data.get("artifact")
            if isinstance(artifact, Artifact):
                artifact_workspace_id = artifact.metadata.get("workspace_id")
                if isinstance(artifact_workspace_id, str):
                    workspace_id = artifact_workspace_id
        if not isinstance(workspace_id, str):
            continue

        entry = workspaces.setdefault(
            workspace_id,
            {"artifacts": [], "snapshots": []},
        )
        provider = data.get("workspace_provider") or metadata_dict.get("workspace_provider")
        if isinstance(provider, str):
            entry["provider"] = provider
        kind = data.get("workspace_kind") or metadata_dict.get("workspace_kind")
        if isinstance(kind, str):
            entry["kind"] = kind
        session_state = data.get("session_state") or metadata_dict.get("workspace_session_state")
        if isinstance(session_state, dict):
            entry["session_state"] = session_state

        artifact = data.get("artifact")
        if isinstance(artifact, Artifact):
            artifacts = cast(list[str], entry.setdefault("artifacts", []))
            if artifact.id not in artifacts:
                artifacts.append(artifact.id)
            if artifact.type == "workspace_snapshot":
                snapshots = cast(list[str], entry.setdefault("snapshots", []))
                if artifact.id not in snapshots:
                    snapshots.append(artifact.id)
    return workspaces


def _prompt_metadata_from_events(events: list[AgentEvent]) -> dict[str, Any]:
    for event in reversed(events):
        if event.type != EventTypes.PROMPT_BUNDLE_CREATED:
            continue
        metadata = event.data.get("metadata")
        return dict(metadata) if isinstance(metadata, dict) else {}
    return {}


async def _provider_cache_metadata(
    *,
    provider: str | None,
    model: str | None,
    cache: ModelCacheControl | None,
    usage: Any,
    provider_cache_store: ProviderCacheStore,
) -> dict[str, Any]:
    if provider is None or model is None:
        return {}
    cache_usage = cache_usage_from_usage(
        provider=provider,
        model=model,
        usage=usage,
        requested=cache is not None,
        key=cache.key if cache is not None else None,
        provider_cache_id=cache.cached_content if cache is not None else None,
        strategy=cache.strategy if cache is not None else None,
        ttl=cache.ttl if cache is not None else None,
    )
    if cache_usage is None:
        return {}
    metadata = cache_usage.to_dict()
    if cache is not None:
        record = await record_provider_cache_usage(
            provider_cache_store,
            provider=provider,
            model=model,
            key=cache.key,
            provider_cache_id=cache.cached_content,
            strategy=cache.strategy,
            ttl=cache.ttl,
            usage=usage,
        )
        if record is not None:
            metadata["record"] = record.to_summary()
    return metadata


def _tool_usage_from_events(events: list[AgentEvent]) -> ModelUsage | None:
    tool_calls = _tool_call_count(events)
    if tool_calls == 0:
        return None
    return ModelUsage(tool_calls=tool_calls)


def _tool_call_count(events: list[AgentEvent]) -> int:
    counted_types = {
        EventTypes.TOOL_CALL_REQUESTED,
        EventTypes.HOSTED_TOOL_CALL_REQUESTED,
        EventTypes.MCP_CALL_STARTED,
        EventTypes.TOOL_SEARCH_REQUESTED,
    }
    seen: set[tuple[str, str]] = set()
    for event in events:
        if event.type not in counted_types:
            continue
        key = _event_call_key(event)
        seen.add((event.type, key))
    return len(seen)


def _event_call_key(event: AgentEvent) -> str:
    for key in ("call_id", "id", "name"):
        value = event.data.get(key)
        if isinstance(value, str) and value:
            return value
    if event.item_id:
        return event.item_id
    return event.id


def _attach_accounting_metadata(metadata: dict[str, Any]) -> None:
    accounting: dict[str, Any] = {}
    for key in ("usage", "cost", "cache"):
        value = metadata.get(key)
        if value is not None:
            accounting[key] = value
    if accounting:
        metadata["accounting"] = accounting


def _event_usage(event: AgentEvent) -> Any:
    usage = event.data.get("usage")
    details = event.data.get("usage_provider_details")
    if isinstance(usage, dict) and isinstance(details, dict) and details:
        return {**usage, "provider_details": details}
    return usage


def _tool_names(tools: Any) -> list[str]:
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
        if isinstance(name, str):
            names.append(name)
    return names


def _finalizer_tool_payload(output_schema: OutputSchema) -> dict[str, Any]:
    description = output_schema.description or "Submit the final structured output."
    return {
        "type": "function",
        "name": _FINALIZER_TOOL_NAME,
        "description": description,
        "parameters": output_schema.schema,
        "metadata": {
            "agent_runtime_hidden": True,
            "purpose": "final_output",
            "schema_name": output_schema.name,
        },
    }


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


def _validate_output(
    text: str,
    output_type: type[Any] | dict[str, Any] | None,
    *,
    allow_dict_schema: bool = False,
) -> Any:
    if output_type is None or output_type is str:
        return text
    if isinstance(output_type, dict):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OutputValidationError(
                "Final output is not valid JSON for the requested JSON Schema.",
                raw_text=text,
                cause=exc,
            ) from exc
        try:
            _validate_json_schema(data, output_type)
        except ValueError as exc:
            raise OutputValidationError(
                f"Final output failed validation against JSON Schema: {exc}",
                raw_text=text,
                cause=exc,
            ) from exc
        return data
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


def _validate_json_schema(value: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']!r}")
    if "anyOf" in schema:
        _validate_any_of(value, schema["anyOf"], path=path)
    if "oneOf" in schema:
        _validate_one_of(value, schema["oneOf"], path=path)

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_json_type(value, expected_type):
        raise ValueError(f"{path} must be {_type_label(expected_type)}")

    if isinstance(value, dict):
        _validate_object(value, schema, path=path)
    elif isinstance(value, list):
        _validate_array(value, schema, path=path)


def _validate_object(value: dict[str, Any], schema: dict[str, Any], *, path: str) -> None:
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in value:
                raise ValueError(f"{path}.{key} is required")
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for key, subschema in properties.items():
            if key in value and isinstance(subschema, dict):
                _validate_json_schema(value[key], subschema, path=f"{path}.{key}")
    additional = schema.get("additionalProperties", True)
    if additional is False and isinstance(properties, dict):
        extras = sorted(set(value) - set(properties))
        if extras:
            raise ValueError(f"{path} contains unknown properties: {', '.join(extras)}")
    elif isinstance(additional, dict):
        for key in set(value) - set(properties if isinstance(properties, dict) else {}):
            _validate_json_schema(value[key], additional, path=f"{path}.{key}")


def _validate_array(value: list[Any], schema: dict[str, Any], *, path: str) -> None:
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        raise ValueError(f"{path} must contain at least {min_items} items")
    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and len(value) > max_items:
        raise ValueError(f"{path} must contain at most {max_items} items")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            _validate_json_schema(item, items, path=f"{path}[{index}]")


def _validate_any_of(value: Any, schemas: Any, *, path: str) -> None:
    if not isinstance(schemas, list):
        return
    for schema in schemas:
        if not isinstance(schema, dict):
            continue
        try:
            _validate_json_schema(value, schema, path=path)
            return
        except ValueError:
            continue
    raise ValueError(f"{path} does not match any allowed schema")


def _validate_one_of(value: Any, schemas: Any, *, path: str) -> None:
    if not isinstance(schemas, list):
        return
    matches = 0
    for schema in schemas:
        if not isinstance(schema, dict):
            continue
        try:
            _validate_json_schema(value, schema, path=path)
        except ValueError:
            continue
        matches += 1
    if matches != 1:
        raise ValueError(f"{path} must match exactly one allowed schema")


def _matches_json_type(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, list):
        return any(_matches_json_type(value, item) for item in expected_type)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _type_label(expected_type: Any) -> str:
    if isinstance(expected_type, list):
        return " or ".join(str(item) for item in expected_type)
    return str(expected_type)
