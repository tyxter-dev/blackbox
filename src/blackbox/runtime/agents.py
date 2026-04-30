from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, TypeVar, cast
from uuid import uuid4

from blackbox.core.accounting import (
    ModelUsage,
    add_usage,
    usage_provider_details,
    usage_to_dict,
)
from blackbox.core.approvals import ApprovalDecision
from blackbox.core.artifacts import Artifact, ArtifactPage, ArtifactRef
from blackbox.core.errors import (
    ApprovalError,
    ConfigurationError,
    SessionError,
    SessionNotFoundError,
    SessionResumeError,
    SessionTerminalError,
)
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.results import (
    AgentMessage,
    AgentResponseSpec,
    AgentSessionResult,
    OutputSpec,
)
from blackbox.core.session_state import (
    SESSION_STATE_SCHEMA_VERSION,
    TERMINAL_SESSION_STATUSES,
    AgentSessionState,
    SessionInvocationState,
)
from blackbox.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from blackbox.core.state import ProviderState, RunState
from blackbox.core.stores import EventStore, RunStore, SessionStore
from blackbox.observability.presets import ObservabilityPreset
from blackbox.observability.traces import TraceContext, trace_metadata_from_events
from blackbox.providers.base import AgentSpec, TaskSpec
from blackbox.providers.registry import ProviderRef, ProviderRegistry
from blackbox.runtime.config import RuntimeConfig, workflow_policy
from blackbox.runtime.event_metadata import (
    _attach_accounting_metadata,
    _event_usage,
    _hosted_tool_metadata_from_events,
    _mcp_metadata_from_events,
    _tool_usage_from_events,
)
from blackbox.runtime.output import _resolve_output_spec, _validate_output
from blackbox.runtime.session_results import (
    _agent_event_text,
    _agent_event_text_delta,
    _agent_message_from_event,
    _agent_session_result_status,
    _agent_task_spec,
    _append_artifact_unique,
    _artifact_from_event,
    _provider_state_from_session,
)
from blackbox.runtime.workspace_results import (
    _agent_event_with_workspace,
    _drain_workspace_events,
    _workspace_metadata_from_events,
    _workspace_ref_metadata,
)
from blackbox.runtime.workspaces import WorkspaceRuntimeFacade
from blackbox.tools.hosted.specs import HostedToolSpec
from blackbox.workspaces.spec import WorkspaceSpec

T = TypeVar("T")


@dataclass(slots=True)
class AgentRuntimeFacade:
    """Facade for provider-managed agent sessions, streaming, and result collection."""

    registry: ProviderRegistry
    event_store: EventStore | None = None
    run_store: RunStore | None = None
    session_store: SessionStore | None = None
    workspaces: WorkspaceRuntimeFacade | None = None
    observability: ObservabilityPreset = field(
        default_factory=ObservabilityPreset.disabled
    )
    _sessions: dict[str, AgentSession] = field(default_factory=dict)
    _live_sessions: set[str] = field(default_factory=set)
    _session_workspaces: dict[str, tuple[Any, Any, bool]] = field(default_factory=dict)

    async def create_agent(self, *, provider: str, spec: AgentSpec) -> AgentRef:
        adapter = self.registry.get_agent(ProviderRef.parse(provider).provider_key)
        return await adapter.create_agent(spec)

    async def create_session(
        self,
        *,
        provider: str | None = None,
        agent: AgentRef | AgentSpec | str | None = None,
        task: str | TaskSpec | None = None,
        config: RuntimeConfig | None = None,
        model: str | None = None,
        workspace: Any | None = None,
        workspace_provider: str | Any | None = None,
        workspace_policy: Any = None,
        workspace_preserve: bool = False,
        inputs: list[ArtifactRef] | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        response: AgentResponseSpec | None = None,
        metadata: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AgentSession:
        """Create a provider-backed agent session and attach a workspace if requested."""

        config_values = config.to_kwargs(surface="agent_session") if config is not None else {}
        provider = cast(str | None, _consume_config_value(config_values, "provider", provider))
        agent = cast(
            AgentRef | AgentSpec | str | None,
            _consume_config_value(config_values, "agent", agent),
        )
        task = cast(
            str | TaskSpec | None,
            _consume_config_value(config_values, "task", task),
        )
        model = cast(str | None, _consume_config_value(config_values, "model", model))
        workspace = _coerce_agent_workspace(
            _consume_config_value(config_values, "workspace", workspace)
        )
        workspace_provider = _consume_config_value(
            config_values, "workspace_provider", workspace_provider
        )
        workspace_policy = workflow_policy(
            _consume_config_value(config_values, "workspace_policy", workspace_policy)
        )
        workspace_preserve = cast(
            bool,
            _consume_config_value(
                config_values, "workspace_preserve", workspace_preserve
            ),
        )
        inputs = cast(
            list[ArtifactRef] | None,
            _consume_config_value(config_values, "inputs", inputs),
        )
        hosted_tools = cast(
            list[HostedToolSpec] | None,
            _consume_config_value(config_values, "hosted_tools", hosted_tools),
        )
        response = cast(
            AgentResponseSpec | None,
            _consume_config_value(config_values, "response", response),
        )
        metadata = cast(
            dict[str, Any] | None,
            _consume_config_value(config_values, "metadata", metadata),
        )
        extra = cast(
            dict[str, Any] | None,
            _consume_config_value(config_values, "extra", extra),
        )
        if provider is None:
            raise ValueError("provider must be provided explicitly or through config.")
        if agent is None:
            raise ValueError("agent must be provided explicitly or through config.")
        if task is None:
            raise ValueError("task must be provided explicitly or through config.")
        adapter = self.registry.get_agent(ProviderRef.parse(provider).provider_key)
        task_spec = _agent_task_spec(
            task,
            model=model,
            workspace=workspace,
            inputs=inputs,
            hosted_tools=hosted_tools,
            response=response,
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
        agent_ref = await _resolve_agent_ref(adapter, agent)
        session = await adapter.start_session(agent_ref, task_spec)
        if resolved_workspace is not None:
            provider_obj, workspace_ref, should_close = resolved_workspace
            self._session_workspaces[session.id] = (provider_obj, workspace_ref, should_close)
            session.metadata.setdefault("workspace", _workspace_ref_metadata(workspace_ref))
            session.metadata.setdefault(
                "workspace_provider", getattr(provider_obj, "provider_id", None)
            )
        self._sessions[session.id] = session
        self._live_sessions.add(session.id)
        if self.session_store is not None:
            await self.session_store.save_session_state(
                _initial_session_state(
                    session=session,
                    agent=agent_ref,
                    task=task_spec,
                    workspace_ref=(
                        _workspace_ref_metadata(resolved_workspace[1])
                        if resolved_workspace is not None
                        else None
                    ),
                )
            )
        return session

    async def load_session(self, session_id: str) -> AgentSession:
        if self.session_store is None:
            raise SessionNotFoundError(
                f"No session store is configured for session {session_id!r}.",
                session_id=session_id,
                operation="load_session",
            )
        state = await self.session_store.load_session_state(session_id)
        if state is None:
            raise SessionNotFoundError(
                f"No persisted session found for {session_id!r}.",
                session_id=session_id,
                operation="load_session",
            )
        return state.session

    async def replay(
        self,
        session: SessionRef | AgentSession | str,
        *,
        after_event_id: str | None = None,
        limit: int | None = None,
    ) -> list[AgentEvent]:
        if self.session_store is None:
            return []
        session_id = session if isinstance(session, str) else session.id
        return await self.session_store.list_session_events(
            session_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    async def resume_session(
        self,
        session_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        session = await self.load_session(session_id)
        async for event in self.stream(session, after_event_id=after_event_id):
            yield event

    async def stream(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Stream a provider-managed session with tracing, storage, and workspace events."""

        state = await self._load_or_create_session_state(session)
        adapter = self.registry.get_agent(state.provider)
        effective_run_id = run_id or f"run_{uuid4().hex}"
        trace_context = TraceContext.for_run(
            trace_id or effective_run_id,
            root_span_id=parent_span_id,
        )
        sequence = 0
        replay_cursor_id = after_event_id
        if after_event_id is not None and self.session_store is not None:
            replayed = await self.session_store.list_session_events(
                state.session.id,
                after_event_id=after_event_id,
            )
            for replayed_event in replayed:
                replay_cursor_id = replayed_event.id
                yield replayed_event
            state = await self._load_required_session_state(state.session)
            if state.status in TERMINAL_SESSION_STATUSES:
                return

        if state.status in TERMINAL_SESSION_STATUSES and state.session.id not in self._live_sessions:
            return

        caps = adapter.capabilities()
        if (
            after_event_id is not None
            and state.session.id not in self._live_sessions
            and not caps.supports_resume
        ):
            raise SessionResumeError(
                f"Provider {state.provider!r} does not support live resume for "
                f"session {state.session.id!r}.",
                session_id=state.session.id,
                provider=state.provider,
                status=state.status,
                operation="stream",
            )

        provider_cursor = await self._provider_cursor_for_resume(
            state.session.id,
            replay_cursor_id,
        )
        provider_session: SessionRef | AgentSession = (
            state.session.ref
            if state.session.id not in self._live_sessions and caps.supports_resume
            else session
        )

        workspace_context = self._session_workspaces.get(state.session.id)
        if workspace_context is not None:
            workspace_provider, workspace_ref, _ = workspace_context
            for workspace_event in _drain_workspace_events(workspace_provider, workspace_ref):
                stamped = trace_context.stamp(
                    workspace_event,
                    run_id=effective_run_id,
                    sequence=sequence,
                )
                sequence += 1
                await self._record_event(stamped)
                await self._persist_session_event(state.session.id, stamped)
                yield stamped
        async for event in adapter.stream_events(provider_session, after_event_id=provider_cursor):
            if workspace_context is not None:
                _, workspace_ref, _ = workspace_context
                event = _agent_event_with_workspace(event, workspace_ref)
            stamped = trace_context.stamp(
                event,
                run_id=effective_run_id,
                sequence=sequence,
            )
            sequence += 1
            await self._record_event(stamped)
            await self._persist_session_event(state.session.id, stamped)
            yield stamped
        if workspace_context is not None:
            workspace_provider, workspace_ref, should_close = workspace_context
            session_obj = self._sessions.get(state.session.id)
            terminal_status = getattr(session_obj or state.session, "status", None)
            if should_close and terminal_status in {"completed", "failed", "cancelled"}:
                await workspace_provider.close(workspace_ref)
                self._session_workspaces.pop(state.session.id, None)
                for workspace_event in _drain_workspace_events(workspace_provider, workspace_ref):
                    stamped = trace_context.stamp(
                        workspace_event,
                        run_id=effective_run_id,
                        sequence=sequence,
                    )
                    sequence += 1
                    await self._record_event(stamped)
                    await self._persist_session_event(state.session.id, stamped)
                    yield stamped

    async def run(
        self,
        *,
        provider: str | None = None,
        agent: AgentRef | AgentSpec | str | None = None,
        task: str | TaskSpec | None = None,
        config: RuntimeConfig | None = None,
        model: str | None = None,
        workspace: Any | None = None,
        workspace_provider: str | Any | None = None,
        workspace_policy: Any = None,
        workspace_preserve: bool = False,
        inputs: list[ArtifactRef] | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        response: AgentResponseSpec | None = None,
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
        config_values = config.to_kwargs(surface="agent_session") if config is not None else {}
        provider = cast(str | None, _consume_config_value(config_values, "provider", provider))
        agent = cast(
            AgentRef | AgentSpec | str | None,
            _consume_config_value(config_values, "agent", agent),
        )
        task = cast(
            str | TaskSpec | None,
            _consume_config_value(config_values, "task", task),
        )
        model = cast(str | None, _consume_config_value(config_values, "model", model))
        workspace = _coerce_agent_workspace(
            _consume_config_value(config_values, "workspace", workspace)
        )
        workspace_provider = _consume_config_value(
            config_values, "workspace_provider", workspace_provider
        )
        workspace_policy = workflow_policy(
            _consume_config_value(config_values, "workspace_policy", workspace_policy)
        )
        workspace_preserve = cast(
            bool,
            _consume_config_value(
                config_values, "workspace_preserve", workspace_preserve
            ),
        )
        inputs = cast(
            list[ArtifactRef] | None,
            _consume_config_value(config_values, "inputs", inputs),
        )
        hosted_tools = cast(
            list[HostedToolSpec] | None,
            _consume_config_value(config_values, "hosted_tools", hosted_tools),
        )
        response = cast(
            AgentResponseSpec | None,
            _consume_config_value(config_values, "response", response),
        )
        metadata = cast(
            dict[str, Any] | None,
            _consume_config_value(config_values, "metadata", metadata),
        )
        extra = cast(
            dict[str, Any] | None,
            _consume_config_value(config_values, "extra", extra),
        )
        output_type = cast(
            type[T] | None,
            _consume_config_value(config_values, "output_type", output_type),
        )
        output_spec = _coerce_agent_output_spec(
            _consume_config_value(config_values, "output_spec", output_spec)
        )
        artifact_type = cast(
            str | None,
            _consume_config_value(config_values, "artifact_type", artifact_type),
        )
        artifact_limit = cast(
            int,
            _consume_config_value(config_values, "artifact_limit", artifact_limit),
        )
        collect_artifacts = cast(
            bool,
            _consume_config_value(
                config_values, "collect_artifacts", collect_artifacts
            ),
        )
        run_id = cast(str | None, _consume_config_value(config_values, "run_id", run_id))
        if provider is None:
            raise ValueError("provider must be provided explicitly or through config.")
        if agent is None:
            raise ValueError("agent must be provided explicitly or through config.")
        if task is None:
            raise ValueError("task must be provided explicitly or through config.")
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
            hosted_tools=hosted_tools,
            response=response,
            metadata=metadata,
            extra=extra,
        )
        events: list[AgentEvent] = []
        text_parts: list[str] = []
        messages: list[AgentMessage] = []
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
            message = _agent_message_from_event(event)
            if message is not None:
                messages.append(message)
            if event.type == EventTypes.SESSION_COMPLETED:
                terminal_text = _agent_event_text(event) or terminal_text
            artifact = _artifact_from_event(event)
            if artifact is not None and (artifact_type is None or artifact.type == artifact_type):
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
        if self.session_store is not None:
            state = await self.session_store.load_session_state(session.id)
            if state is not None:
                state.artifact_refs = [artifact.ref for artifact in artifacts]
                state.provider_state = captured_state or state.provider_state
                state.status = session.status
                state.session.status = session.status
                await self.session_store.save_session_state(state)

        text = terminal_text if terminal_text is not None else "".join(text_parts)
        spec = _resolve_output_spec(output_type, output_spec)
        output = _validate_output(text, spec.schema, allow_dict_schema=True)
        usage = add_usage(usage, _tool_usage_from_events(events))
        result_status = _agent_session_result_status(session, events)
        provider_state = captured_state or _provider_state_from_session(session, events=events)
        usage_dict = usage_to_dict(usage)
        trace = trace_metadata_from_events(
            events, metadata={"usage": usage_dict} if usage_dict else {}
        )
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
        if messages:
            result_metadata["message_count"] = len(messages)
        hosted_metadata = _hosted_tool_metadata_from_events(events)
        if hosted_metadata:
            result_metadata["hosted_tools"] = hosted_metadata
        mcp_metadata = _mcp_metadata_from_events(events)
        if mcp_metadata:
            result_metadata["mcp"] = mcp_metadata
        if trace["spans"]:
            result_metadata["trace"] = trace
        workspace_metadata = _workspace_metadata_from_events(events)
        if workspace_metadata:
            result_metadata["workspaces"] = workspace_metadata
        _attach_accounting_metadata(result_metadata)
        self.observability.export_run(events, metadata=result_metadata)

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
            messages=messages,
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
        self,
        session: SessionRef | AgentSession,
        message: str,
        *,
        idempotency_key: str | None = None,
    ) -> InvocationRef:
        state = await self._load_required_session_state(session)
        if idempotency_key is not None:
            existing_id = state.idempotency_keys.get(idempotency_key)
            if existing_id is not None:
                for existing_invocation in state.invocations:
                    if existing_invocation.id == existing_id:
                        return InvocationRef(
                            provider=state.provider,
                            session_id=state.session.id,
                            id=existing_invocation.id,
                            metadata=existing_invocation.metadata,
                        )
        if state.status == "created":
            raise SessionError(
                f"Cannot send a follow-up to session {state.session.id!r} before it starts."
            )
        if state.status == "waiting":
            raise ApprovalError("Session is waiting for approval; use approve(...).")
        if state.status in {"failed", "cancelled"}:
            raise SessionTerminalError(
                f"Cannot send a follow-up to terminal session status {state.status!r}.",
                session_id=state.session.id,
                provider=state.provider,
                status=state.status,
                operation="send_message",
            )
        adapter = self.registry.get_agent(state.provider)
        invocation_ref = await adapter.send_message(state.session, message)
        raw_provider_invocation_id = invocation_ref.metadata.get("provider_invocation_id")
        provider_invocation_id = (
            raw_provider_invocation_id if isinstance(raw_provider_invocation_id, str) else None
        )
        invocation_state = SessionInvocationState(
            id=invocation_ref.id,
            session_id=state.session.id,
            provider=state.provider,
            status="queued" if state.status == "running" else "running",
            message=message,
            idempotency_key=idempotency_key,
            provider_invocation_id=provider_invocation_id,
            started_at=datetime.now(UTC).isoformat(),
            metadata=dict(invocation_ref.metadata),
        )
        state.invocations.append(invocation_state)
        if idempotency_key is not None:
            state.idempotency_keys[idempotency_key] = invocation_ref.id
        state.status = "running"
        state.session.status = "running"
        await self._save_session_state(state)
        return invocation_ref

    async def approve(self, provider: str, approval_id: str, decision: ApprovalDecision) -> None:
        state = (
            await self.session_store.find_session_by_approval(approval_id)
            if self.session_store is not None
            else None
        )
        if state is not None:
            pending = state.pending_approvals[approval_id]
            desired_status = "approved" if decision.approved else "denied"
            if pending.status != "pending":
                if pending.status == desired_status:
                    return
                raise ApprovalError(
                    f"Approval {approval_id!r} was already {pending.status}; "
                    f"cannot mark it {desired_status}."
                )
            provider_key = state.provider
        else:
            provider_key = ProviderRef.parse(provider).provider_key
        adapter = self.registry.get_agent(provider_key)
        adapter_error: Exception | None = None
        try:
            await adapter.approve(approval_id, decision)
            if state is not None:
                pending = state.pending_approvals[approval_id]
                pending.status = "approved" if decision.approved else "denied"
                state.status = "running"
                state.session.status = "running"
                await self._save_session_state(state)
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
        state = await self._load_required_session_state(session)
        if state.status in TERMINAL_SESSION_STATUSES:
            return
        state.metadata["cancel_requested"] = True
        await self._save_session_state(state)
        adapter = self.registry.get_agent(state.provider)
        await adapter.cancel(state.session)
        state.status = "cancelled"
        state.session.status = "cancelled"
        await self._save_session_state(state)

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
            session,
            type=type,
            after=after,
            limit=limit,
        )

    async def _load_or_create_session_state(
        self,
        session: SessionRef | AgentSession,
    ) -> AgentSessionState:
        if self.session_store is not None:
            state = await self.session_store.load_session_state(session.id)
            if state is not None:
                return state
        if isinstance(session, AgentSession):
            state = _initial_session_state(
                session=session,
                agent=AgentRef(
                    provider=session.provider,
                    id=session.agent_id or "",
                ) if session.agent_id else None,
                task=TaskSpec(prompt=session.task, model=session.model),
                workspace_ref=(
                    dict(session.metadata["workspace"])
                    if isinstance(session.metadata.get("workspace"), dict)
                    else None
                ),
            )
            await self._save_session_state(state)
            return state
        raise SessionNotFoundError(
            f"No persisted session found for {session.id!r}.",
            session_id=session.id,
            provider=session.provider,
            operation="stream",
        )

    async def _load_required_session_state(
        self,
        session: SessionRef | AgentSession,
    ) -> AgentSessionState:
        if self.session_store is not None:
            state = await self.session_store.load_session_state(session.id)
            if state is not None:
                return state
        if isinstance(session, AgentSession):
            return await self._load_or_create_session_state(session)
        raise SessionNotFoundError(
            f"No persisted session found for {session.id!r}.",
            session_id=session.id,
            provider=session.provider,
            operation="load_session_state",
        )

    async def _save_session_state(self, state: AgentSessionState) -> None:
        if self.session_store is not None:
            await self.session_store.save_session_state(state)

    async def _record_event(self, event: AgentEvent) -> None:
        if self.event_store is not None:
            await self.event_store.append(event)
        await self.observability.emit_event(event)

    async def _persist_session_event(self, session_id: str, event: AgentEvent) -> None:
        if self.session_store is None:
            return
        provider_event_id = _provider_event_id(event)
        await self.session_store.append_session_event(
            session_id,
            _session_scoped_event(event, session_id=session_id),
            provider_event_id=provider_event_id,
            provider_cursor=_provider_cursor(event, provider_event_id),
        )

    async def _provider_cursor_for_resume(
        self,
        session_id: str,
        event_id: str | None,
    ) -> str | None:
        if event_id is None or self.session_store is None:
            return event_id
        cursor = await self.session_store.get_event_cursor(session_id, event_id)
        if cursor is None:
            return event_id
        return cursor.provider_cursor or cursor.provider_event_id or cursor.event_id


async def _resolve_agent_ref(adapter: Any, agent: AgentRef | AgentSpec | str) -> AgentRef | str:
    if isinstance(agent, AgentSpec):
        return cast(AgentRef, await adapter.create_agent(agent))
    return agent


def _consume_config_value(
    values: dict[str, Any],
    name: str,
    explicit: Any,
) -> Any:
    configured = values.pop(name, None)
    return explicit if explicit is not None else configured


def _coerce_agent_workspace(value: Any) -> Any | None:
    if value is None or isinstance(value, WorkspaceSpec):
        return value
    if isinstance(value, Mapping):
        return WorkspaceSpec(**dict(value))
    return value


def _coerce_agent_output_spec(value: Any) -> OutputSpec | None:
    if value is None or isinstance(value, OutputSpec):
        return value
    if isinstance(value, Mapping):
        return OutputSpec(**dict(value))
    raise ConfigurationError(f"Invalid output_spec config: {value!r}.")


def _initial_session_state(
    *,
    session: AgentSession,
    agent: AgentRef | str | None,
    task: TaskSpec,
    workspace_ref: dict[str, Any] | None,
) -> AgentSessionState:
    provider_session_id = _metadata_str(session.metadata, "provider_session_id")
    runtime_session_id = _metadata_str(session.metadata, "runtime_session_id") or session.id
    last_event_id = _metadata_str(session.metadata, "last_event_id")
    provider_state = ProviderState(
        provider=session.provider,
        conversation_id=(
            _metadata_str(session.metadata, "conversation_id")
            or provider_session_id
            or session.id
        ),
        continuation={
            "session_id": session.id,
            "provider_session_id": provider_session_id,
            "runtime_session_id": runtime_session_id,
            "last_event_id": last_event_id,
        },
    )
    agent_ref = agent if isinstance(agent, AgentRef) else None
    created_at = datetime.now(UTC).isoformat()
    state = AgentSessionState(
        schema_version=SESSION_STATE_SCHEMA_VERSION,
        session=session,
        session_ref=session.ref,
        provider=session.provider,
        status=session.status,
        agent_ref=agent_ref,
        task=task.prompt,
        model=session.model or task.model,
        provider_state=provider_state,
        workspace_ref=workspace_ref,
        created_at=created_at,
        updated_at=created_at,
        metadata={
            "task_metadata": dict(task.metadata),
            "task_extra": dict(task.extra),
        },
    )
    state.invocations.append(
        SessionInvocationState(
            id=str(session.metadata.get("provider_invocation_id") or f"inv_{session.id}_initial"),
            session_id=session.id,
            provider=session.provider,
            status="created" if session.status == "created" else "running",
            message=task.prompt,
            provider_invocation_id=_metadata_str(session.metadata, "provider_invocation_id"),
            started_at=created_at if session.status != "created" else None,
            metadata={"initial": True},
        )
    )
    return state


def _session_scoped_event(event: AgentEvent, *, session_id: str) -> AgentEvent:
    if event.session_id == session_id:
        return event
    return replace(event, session_id=session_id)


def _provider_event_id(event: AgentEvent) -> str | None:
    value = event.data.get("provider_event_id")
    if isinstance(value, str):
        return value
    raw = event.raw
    if isinstance(raw, dict):
        for key in ("provider_event_id", "event_id", "id"):
            raw_value = raw.get(key)
            if isinstance(raw_value, str):
                return raw_value
    return event.id


def _provider_cursor(
    event: AgentEvent,
    provider_event_id: str | None,
) -> str | None:
    value = event.data.get("provider_cursor")
    if isinstance(value, str):
        return value
    value = event.data.get("cursor")
    if isinstance(value, str):
        return value
    return provider_event_id


def _metadata_str(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None
