from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactPage, ArtifactRef
from agent_runtime.core.errors import ApprovalError
from agent_runtime.core.events import AgentEvent
from agent_runtime.workspaces.changes import (
    CommandResult,
    CommandSpec,
    FileChange,
    Patch,
    PatchArtifact,
)
from agent_runtime.workspaces.spec import (
    WorkspacePort,
    WorkspaceProviderCapabilities,
    WorkspaceRef,
    WorkspaceSessionState,
    WorkspaceSpec,
)


@dataclass(slots=True)
class PendingWorkspaceOperation:
    id: str
    workspace_id: str
    provider: str
    operation: str
    arguments: dict[str, Any]
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkspaceApprovalRequired(ApprovalError):  # noqa: N818
    def __init__(
        self,
        pending: PendingWorkspaceOperation,
        *,
        approve: Callable[[str, ApprovalDecision], Awaitable[None]] | None = None,
    ) -> None:
        self.pending = pending
        self._approve = approve
        super().__init__(
            pending.reason or f"Workspace operation requires approval: {pending.operation}"
        )

    async def approve(self, decision: ApprovalDecision) -> None:
        if self._approve is not None:
            await self._approve(self.pending.id, decision)


@runtime_checkable
class WorkspaceProvider(Protocol):
    """Protocol for providers that expose file, command, artifact, and approval workspace operations."""

    @property
    def provider_id(self) -> str:
        ...

    def capabilities(self) -> WorkspaceProviderCapabilities:
        ...

    async def open(self, spec: WorkspaceSpec) -> WorkspaceRef:
        ...

    async def attach(self, state: WorkspaceSessionState) -> WorkspaceRef:
        ...

    async def close(self, ws: WorkspaceRef, *, delete: bool = False) -> None:
        """Close an open workspace and optionally delete provider-owned resources."""
        ...

    async def read_file(self, ws: WorkspaceRef, path: str) -> str:
        """Return text content from a workspace-relative file path."""
        ...

    async def write_file(self, ws: WorkspaceRef, path: str, content: str) -> FileChange:
        """Write text content to a workspace-relative file path and report the change."""
        ...

    async def delete_file(self, ws: WorkspaceRef, path: str) -> FileChange:
        """Delete a workspace-relative file path and report the change."""
        ...

    async def list_files(
        self,
        ws: WorkspaceRef,
        *,
        path: str = ".",
        recursive: bool = False,
        limit: int = 1000,
    ) -> list[str]:
        """List workspace-relative file paths under a directory."""
        ...

    async def apply_patch(self, ws: WorkspaceRef, patch: Patch) -> PatchArtifact:
        """Apply a structured patch and return its patch artifact."""
        ...

    async def run_command(self, ws: WorkspaceRef, spec: CommandSpec) -> CommandResult:
        """Run a command in the workspace and return the completed result."""
        ...

    def stream_command(
        self,
        ws: WorkspaceRef,
        spec: CommandSpec,
    ) -> AsyncIterator[AgentEvent]:
        """Run a command in the workspace and stream workspace events while it executes."""
        ...

    async def snapshot(self, ws: WorkspaceRef, *, name: str | None = None) -> Artifact:
        """Create an artifact that captures the workspace state."""
        ...

    async def restore(
        self,
        snapshot: ArtifactRef,
        *,
        spec: WorkspaceSpec | None = None,
    ) -> WorkspaceRef:
        """Restore a workspace from a snapshot artifact, optionally using a new spec."""
        ...

    async def expose_port(
        self,
        ws: WorkspaceRef,
        port: int,
        *,
        protocol: str = "http",
        name: str | None = None,
    ) -> WorkspacePort:
        """Expose a workspace network port and return the provider-published endpoint."""
        ...

    async def list_artifacts(
        self,
        ws: WorkspaceRef,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        """Return a paginated list of artifacts associated with a workspace."""
        ...

    async def export_artifact(self, ws: WorkspaceRef, ref: ArtifactRef) -> Artifact:
        """Resolve a provider artifact reference into an exported artifact payload."""
        ...

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        """Apply an approval decision to a pending workspace operation."""
        ...

    def session_state(self, ws: WorkspaceRef) -> WorkspaceSessionState:
        ...

    def drain_events(self) -> list[AgentEvent]:
        ...
