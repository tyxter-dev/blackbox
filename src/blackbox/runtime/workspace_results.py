from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from blackbox.core.artifacts import Artifact
from blackbox.core.events import AgentEvent, EventTypes


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
        from blackbox.workspaces.serialization import workspace_state_to_dict

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
