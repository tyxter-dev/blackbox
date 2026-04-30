from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from blackbox.core.artifacts import Artifact, ArtifactRef
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.sessions import AgentRef, AgentSession, SessionRef
from blackbox.core.state import ProviderState

SESSION_STATE_SCHEMA_VERSION = 1
TERMINAL_SESSION_STATUSES = {"completed", "failed", "cancelled"}


@dataclass(slots=True)
class SessionEventCursor:
    """Runtime and provider-native cursor metadata for one session event."""

    session_id: str
    event_id: str
    session_sequence: int
    run_id: str | None = None
    run_sequence: int | None = None
    provider_event_id: str | None = None
    provider_cursor: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionInvocationState:
    """Durable state for an initial task or follow-up message invocation."""

    id: str
    session_id: str
    provider: str
    status: str = "created"
    message: str | None = None
    idempotency_key: str | None = None
    provider_invocation_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    last_event_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PendingApprovalState:
    """Durable state for a provider approval request."""

    approval_id: str
    session_id: str
    status: str = "pending"
    action: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    provider_approval_id: str | None = None
    requested_event_id: str | None = None
    resolved_event_id: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentSessionState:
    """Durable state for a provider-managed agent session."""

    schema_version: int
    session: AgentSession
    session_ref: SessionRef
    provider: str
    status: str
    agent_ref: AgentRef | None = None
    task: str | None = None
    model: str | None = None
    provider_state: ProviderState | None = None
    run_ids: list[str] = field(default_factory=list)
    current_run_id: str | None = None
    trace_id: str | None = None
    last_event_id: str | None = None
    last_session_sequence: int = -1
    last_provider_event_id: str | None = None
    last_provider_cursor: str | None = None
    invocations: list[SessionInvocationState] = field(default_factory=list)
    pending_approvals: dict[str, PendingApprovalState] = field(default_factory=dict)
    workspace_ref: dict[str, Any] | None = None
    workspace_state: dict[str, Any] = field(default_factory=dict)
    mcp_state: dict[str, Any] = field(default_factory=dict)
    artifact_refs: list[ArtifactRef] = field(default_factory=list)
    idempotency_keys: dict[str, str] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def apply_session_event(
    state: AgentSessionState,
    event: AgentEvent,
    cursor: SessionEventCursor,
) -> AgentSessionState:
    """Apply a normalized event to the durable session snapshot."""

    timestamp = event.timestamp.isoformat()
    if state.created_at is None:
        state.created_at = timestamp
    state.updated_at = timestamp
    state.last_event_id = cursor.event_id
    state.last_session_sequence = cursor.session_sequence
    state.last_provider_event_id = cursor.provider_event_id
    state.last_provider_cursor = cursor.provider_cursor
    if event.run_id is not None:
        state.current_run_id = event.run_id
        if event.run_id not in state.run_ids:
            state.run_ids.append(event.run_id)
    if event.trace_id is not None:
        state.trace_id = event.trace_id

    if event.type == EventTypes.SESSION_STARTED:
        _set_status(state, "running")
    elif event.type == EventTypes.CLOUD_AGENT_STATUS_CHANGED:
        event_status = event.data.get("status")
        if isinstance(event_status, str) and event_status in {
            "created",
            "running",
            "waiting",
            "completed",
            "failed",
            "cancelled",
        }:
            _set_status(state, event_status)
        elif state.status != "waiting":
            _set_status(state, "running")
    elif event.type == EventTypes.APPROVAL_REQUESTED:
        _set_status(state, "waiting")
        approval = _approval_from_event(state.session.id, event)
        if approval is not None:
            state.pending_approvals[approval.approval_id] = approval
    elif event.type == EventTypes.APPROVAL_APPROVED:
        _resolve_approval(state, event, "approved")
        _set_status(state, "running")
    elif event.type == EventTypes.APPROVAL_DENIED:
        _resolve_approval(state, event, "denied")
    elif event.type == EventTypes.ARTIFACT_CREATED:
        artifact_ref = _artifact_ref_from_event(event)
        if artifact_ref is not None:
            _append_artifact_ref(state, artifact_ref)
    elif event.type.startswith("workspace."):
        state.workspace_state["last_event_id"] = event.id
        state.workspace_state["last_event_type"] = event.type
        _merge_event_data(state.workspace_state, event.data)
    elif event.type.startswith("mcp."):
        state.mcp_state["last_event_id"] = event.id
        state.mcp_state["last_event_type"] = event.type
        _merge_event_data(state.mcp_state, event.data)
    elif event.type == EventTypes.SESSION_COMPLETED:
        _set_status(state, "completed")
        state.completed_at = timestamp
    elif event.type == EventTypes.SESSION_FAILED:
        _set_status(state, "failed")
        state.completed_at = timestamp
    elif event.type == EventTypes.SESSION_CANCELLED:
        _set_status(state, "cancelled")
        state.completed_at = timestamp

    provider_state = event.data.get("provider_state")
    if isinstance(provider_state, ProviderState):
        state.provider_state = provider_state
    invocation_id = event.data.get("invocation_id")
    if isinstance(invocation_id, str):
        _update_invocation_from_event(state, invocation_id, event)
    return state


def _set_status(state: AgentSessionState, status: str) -> None:
    state.status = status
    if status in {"created", "running", "waiting", "completed", "failed", "cancelled"}:
        state.session.status = status  # type: ignore[assignment]
    state.session.metadata["status"] = status


def _approval_from_event(
    session_id: str,
    event: AgentEvent,
) -> PendingApprovalState | None:
    approval_id = event.data.get("approval_id") or event.item_id
    if not isinstance(approval_id, str) or not approval_id:
        return None
    arguments = event.data.get("arguments")
    provider_approval_id = event.data.get("provider_approval_id")
    return PendingApprovalState(
        approval_id=approval_id,
        session_id=session_id,
        action=event.data.get("action") if isinstance(event.data.get("action"), str) else None,
        arguments=arguments if isinstance(arguments, dict) else {},
        provider_approval_id=provider_approval_id if isinstance(provider_approval_id, str) else None,
        requested_event_id=event.id,
        metadata={"event_type": event.type},
    )


def _resolve_approval(
    state: AgentSessionState,
    event: AgentEvent,
    status: str,
) -> None:
    approval_id = event.data.get("approval_id") or event.item_id
    if not isinstance(approval_id, str):
        return
    approval = state.pending_approvals.get(approval_id)
    if approval is None:
        approval = PendingApprovalState(approval_id=approval_id, session_id=state.session.id)
        state.pending_approvals[approval_id] = approval
    approval.status = status
    approval.resolved_event_id = event.id


def _artifact_ref_from_event(event: AgentEvent) -> ArtifactRef | None:
    artifact = event.data.get("artifact")
    if isinstance(artifact, Artifact):
        return artifact.ref
    if isinstance(artifact, ArtifactRef):
        return artifact
    if isinstance(artifact, dict):
        artifact_id = artifact.get("id")
        if isinstance(artifact_id, str):
            provider = artifact.get("provider")
            uri = artifact.get("uri")
            return ArtifactRef(
                id=artifact_id,
                provider=provider if isinstance(provider, str) else event.provider,
                uri=uri if isinstance(uri, str) else None,
            )
    artifact_id = event.data.get("artifact_id") or event.item_id
    if isinstance(artifact_id, str):
        return ArtifactRef(id=artifact_id, provider=event.provider)
    return None


def _append_artifact_ref(state: AgentSessionState, artifact_ref: ArtifactRef) -> None:
    if all(existing.id != artifact_ref.id for existing in state.artifact_refs):
        state.artifact_refs.append(artifact_ref)


def _merge_event_data(target: dict[str, Any], data: dict[str, Any]) -> None:
    for key, value in data.items():
        if key in {"credentials", "secret", "token", "api_key", "raw"}:
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            target[key] = value
        elif isinstance(value, dict):
            nested = target.get(key)
            if not isinstance(nested, dict):
                nested = {}
                target[key] = nested
            _merge_event_data(nested, value)


def _update_invocation_from_event(
    state: AgentSessionState,
    invocation_id: str,
    event: AgentEvent,
) -> None:
    for invocation in state.invocations:
        if invocation.id != invocation_id:
            continue
        invocation.last_event_id = event.id
        if event.type == EventTypes.SESSION_STARTED and invocation.started_at is None:
            invocation.started_at = event.timestamp.isoformat()
            invocation.status = "running"
        elif event.type in {
            EventTypes.SESSION_COMPLETED,
            EventTypes.SESSION_FAILED,
            EventTypes.SESSION_CANCELLED,
        }:
            invocation.completed_at = event.timestamp.isoformat()
            invocation.status = {
                EventTypes.SESSION_COMPLETED: "completed",
                EventTypes.SESSION_FAILED: "failed",
                EventTypes.SESSION_CANCELLED: "cancelled",
            }[event.type]
        return
