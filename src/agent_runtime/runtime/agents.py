from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar, cast
from uuid import uuid4

from agent_runtime.core.accounting import (
    ModelUsage,
    add_usage,
    usage_provider_details,
    usage_to_dict,
)
from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactPage, ArtifactRef
from agent_runtime.core.errors import ApprovalError, ConfigurationError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.results import AgentSessionResult, OutputSpec
from agent_runtime.core.sessions import AgentRef, AgentSession, InvocationRef, SessionRef
from agent_runtime.core.state import ProviderState, RunState
from agent_runtime.core.stores import EventStore, RunStore
from agent_runtime.observability.traces import TraceContext, trace_metadata_from_events
from agent_runtime.providers.base import AgentSpec, TaskSpec
from agent_runtime.providers.registry import ProviderRef, ProviderRegistry
from agent_runtime.runtime.event_metadata import (
    _attach_accounting_metadata,
    _event_usage,
    _tool_usage_from_events,
)
from agent_runtime.runtime.output import _resolve_output_spec, _validate_output
from agent_runtime.runtime.session_results import (
    _agent_event_text,
    _agent_event_text_delta,
    _agent_session_result_status,
    _agent_task_spec,
    _append_artifact_unique,
    _artifact_from_event,
    _provider_state_from_session,
)
from agent_runtime.runtime.workspace_results import (
    _agent_event_with_workspace,
    _drain_workspace_events,
    _workspace_metadata_from_events,
    _workspace_ref_metadata,
)
from agent_runtime.runtime.workspaces import WorkspaceRuntimeFacade

T = TypeVar("T")


@dataclass(slots=True)
class AgentRuntimeFacade:
    """Facade for provider-managed agent sessions, streaming, and result collection."""

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
        """Create a provider-backed agent session and attach a workspace if requested."""

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
        """Stream a provider-managed session with tracing, storage, and workspace events."""

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
