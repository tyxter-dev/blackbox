from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from agent_runtime.core.artifacts import ArtifactRef

WorkspaceKind = Literal["local", "git", "sandbox", "docker", "cloud"]
WorkspaceNetwork = Literal["disabled", "default"]
WorkspacePortProtocol = Literal["http", "https", "tcp"]


@dataclass(slots=True, frozen=True)
class WorkspaceProviderCapabilities:
    supports_local_files: bool = False
    supports_sandbox: bool = False
    supports_docker: bool = False
    supports_git_sources: bool = False
    supports_cloud_refs: bool = False
    supports_commands: bool = False
    supports_streaming_command_output: bool = False
    supports_patches: bool = False
    supports_snapshots: bool = False
    supports_restore: bool = False
    supports_artifacts: bool = False
    supports_ports: bool = False
    supports_approvals: bool = False
    supports_resume: bool = False


@dataclass(slots=True, frozen=True)
class WorkspaceSessionState:
    provider: str
    workspace_id: str
    kind: WorkspaceKind
    provider_workspace_id: str | None = None
    provider_session_id: str | None = None
    root: str | None = None
    snapshot_id: str | None = None
    serialized_state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WorkspaceRef:
    """Stable handle for an opened workspace.

    ``root`` is populated for local workspaces and may be a provider-native
    root for sandbox/cloud workspaces. Remote providers should preserve their
    native identifiers in ``provider_workspace_id``, ``provider_session_id``,
    ``session_state``, or ``metadata``.
    """

    id: str = field(default_factory=lambda: f"ws_{uuid4().hex}")
    kind: WorkspaceKind = "local"
    provider: str = "workspace"
    root: str | None = None
    name: str | None = None
    provider_workspace_id: str | None = None
    provider_session_id: str | None = None
    session_state: WorkspaceSessionState | None = None
    snapshot: ArtifactRef | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def local(
        cls,
        root: str | Path,
        *,
        id: str | None = None,
        provider: str = "local-workspace",
    ) -> WorkspaceRef:
        return cls(
            id=id or f"ws_{uuid4().hex}",
            kind="local",
            provider=provider,
            root=str(root),
        )


@dataclass(slots=True, frozen=True)
class WorkspaceMount:
    """A mount of a workspace into an agent's environment.

    ``target_path`` is the location inside the agent's runtime where the
    workspace appears (e.g. ``/workspace`` for sandboxed agents). ``read_only``
    instructs the runtime to refuse writes regardless of policy outcomes.
    """

    workspace: WorkspaceRef
    target_path: str | None = None
    read_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WorkspaceSpec:
    kind: WorkspaceKind
    root: str | None = None
    repo: str | None = None
    branch: str | None = None
    url: str | None = None
    ref: str | None = None
    inputs: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    mounts: list[WorkspaceMount] = field(default_factory=list)
    image: str | None = None
    user: str | None = None
    workdir: str | None = None
    network: WorkspaceNetwork = "disabled"
    resources: dict[str, Any] = field(default_factory=dict)
    snapshot: ArtifactRef | None = None
    session_state: WorkspaceSessionState | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def local(
        cls,
        root: str | Path,
        *,
        env: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceSpec:
        return cls(kind="local", root=str(root), env=env or {}, metadata=metadata or {})

    @classmethod
    def git(
        cls,
        repo: str | None = None,
        *,
        url: str | None = None,
        ref: str | None = None,
        branch: str | None = None,
        root: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceSpec:
        source = repo or url
        if source is None:
            raise ValueError("WorkspaceSpec.git requires repo or url.")
        checkout = branch or ref
        return cls(
            kind="git",
            root=root,
            repo=source,
            branch=checkout,
            url=url or source,
            ref=ref or checkout,
            metadata=metadata or {},
        )

    @classmethod
    def sandbox(
        cls,
        *,
        root: str = "/workspace",
        image: str | None = None,
        inputs: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        mounts: list[WorkspaceMount] | None = None,
        snapshot: ArtifactRef | None = None,
        session_state: WorkspaceSessionState | None = None,
        metadata: dict[str, Any] | None = None,
        user: str | None = None,
        workdir: str | None = None,
        network: WorkspaceNetwork = "disabled",
        resources: dict[str, Any] | None = None,
    ) -> WorkspaceSpec:
        return cls(
            kind="sandbox",
            root=root,
            image=image,
            inputs=inputs or {},
            env=env or {},
            mounts=mounts or [],
            snapshot=snapshot,
            session_state=session_state,
            metadata=metadata or {},
            user=user,
            workdir=workdir,
            network=network,
            resources=resources or {},
        )

    @classmethod
    def docker(
        cls,
        *,
        image: str,
        root: str = "/workspace",
        inputs: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        mounts: list[WorkspaceMount] | None = None,
        snapshot: ArtifactRef | None = None,
        session_state: WorkspaceSessionState | None = None,
        metadata: dict[str, Any] | None = None,
        user: str | None = None,
        workdir: str | None = None,
        network: WorkspaceNetwork = "disabled",
        resources: dict[str, Any] | None = None,
    ) -> WorkspaceSpec:
        return cls(
            kind="docker",
            root=root,
            image=image,
            inputs=inputs or {},
            env=env or {},
            mounts=mounts or [],
            snapshot=snapshot,
            session_state=session_state,
            metadata=metadata or {},
            user=user,
            workdir=workdir,
            network=network,
            resources=resources or {},
        )

    @classmethod
    def cloud(
        cls,
        *,
        provider_workspace_id: str | None = None,
        provider_session_id: str | None = None,
        root: str | None = None,
        session_state: WorkspaceSessionState | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceSpec:
        cloud_metadata = dict(metadata or {})
        if provider_workspace_id is not None:
            cloud_metadata.setdefault("provider_workspace_id", provider_workspace_id)
        if provider_session_id is not None:
            cloud_metadata.setdefault("provider_session_id", provider_session_id)
        return cls(
            kind="cloud",
            root=root,
            session_state=session_state,
            metadata=cloud_metadata,
        )


@dataclass(slots=True, frozen=True)
class WorkspacePort:
    id: str = field(default_factory=lambda: f"port_{uuid4().hex}")
    workspace_id: str = ""
    port: int = 0
    protocol: WorkspacePortProtocol = "http"
    host: str | None = None
    url: str | None = None
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
