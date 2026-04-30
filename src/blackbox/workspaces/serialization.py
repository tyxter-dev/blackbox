from __future__ import annotations

from typing import Any

from blackbox.core.artifacts import Artifact, ArtifactRef
from blackbox.workspaces.spec import (
    WorkspaceRef,
    WorkspaceSessionState,
)


def artifact_ref_to_dict(ref: ArtifactRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    return {"id": ref.id, "provider": ref.provider, "uri": ref.uri}


def artifact_ref_from_dict(payload: dict[str, Any] | None) -> ArtifactRef | None:
    if payload is None:
        return None
    return ArtifactRef(
        id=str(payload.get("id") or ""),
        provider=_optional_str(payload.get("provider")),
        uri=_optional_str(payload.get("uri")),
    )


def artifact_to_dict(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "type": artifact.type,
        "name": artifact.name,
        "data": artifact.data,
        "uri": artifact.uri,
        "metadata": dict(artifact.metadata),
    }


def workspace_state_to_dict(state: WorkspaceSessionState) -> dict[str, Any]:
    return {
        "provider": state.provider,
        "workspace_id": state.workspace_id,
        "kind": state.kind,
        "provider_workspace_id": state.provider_workspace_id,
        "provider_session_id": state.provider_session_id,
        "root": state.root,
        "snapshot_id": state.snapshot_id,
        "serialized_state": dict(state.serialized_state),
        "metadata": dict(state.metadata),
    }


def workspace_state_from_dict(payload: dict[str, Any]) -> WorkspaceSessionState:
    return WorkspaceSessionState(
        provider=str(payload.get("provider") or "workspace"),
        workspace_id=str(payload.get("workspace_id") or ""),
        kind=payload.get("kind", "local"),
        provider_workspace_id=_optional_str(payload.get("provider_workspace_id")),
        provider_session_id=_optional_str(payload.get("provider_session_id")),
        root=_optional_str(payload.get("root")),
        snapshot_id=_optional_str(payload.get("snapshot_id")),
        serialized_state=dict(payload.get("serialized_state") or {}),
        metadata=dict(payload.get("metadata") or {}),
    )


def workspace_ref_to_dict(ref: WorkspaceRef) -> dict[str, Any]:
    return {
        "id": ref.id,
        "kind": ref.kind,
        "provider": ref.provider,
        "root": ref.root,
        "name": ref.name,
        "provider_workspace_id": ref.provider_workspace_id,
        "provider_session_id": ref.provider_session_id,
        "session_state": (
            workspace_state_to_dict(ref.session_state) if ref.session_state is not None else None
        ),
        "snapshot": artifact_ref_to_dict(ref.snapshot),
        "metadata": dict(ref.metadata),
    }


def workspace_ref_from_dict(payload: dict[str, Any]) -> WorkspaceRef:
    state_raw = payload.get("session_state")
    state = workspace_state_from_dict(state_raw) if isinstance(state_raw, dict) else None
    snapshot_raw = payload.get("snapshot")
    snapshot = artifact_ref_from_dict(snapshot_raw) if isinstance(snapshot_raw, dict) else None
    return WorkspaceRef(
        id=str(payload.get("id") or ""),
        kind=payload.get("kind", "local"),
        provider=str(payload.get("provider") or "workspace"),
        root=_optional_str(payload.get("root")),
        name=_optional_str(payload.get("name")),
        provider_workspace_id=_optional_str(payload.get("provider_workspace_id")),
        provider_session_id=_optional_str(payload.get("provider_session_id")),
        session_state=state,
        snapshot=snapshot,
        metadata=dict(payload.get("metadata") or {}),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
