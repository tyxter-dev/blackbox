from __future__ import annotations

from dataclasses import replace
from typing import Any

from agent_runtime.core.artifacts import Artifact, ArtifactRef
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.results import AgentSessionResultStatus
from agent_runtime.core.sessions import AgentSession
from agent_runtime.core.state import ProviderState
from agent_runtime.providers.base import TaskSpec


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
