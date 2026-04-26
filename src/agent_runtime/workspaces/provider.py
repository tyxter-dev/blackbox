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
        ...

    async def read_file(self, ws: WorkspaceRef, path: str) -> str:
        ...

    async def write_file(self, ws: WorkspaceRef, path: str, content: str) -> FileChange:
        ...

    async def delete_file(self, ws: WorkspaceRef, path: str) -> FileChange:
        ...

    async def list_files(
        self,
        ws: WorkspaceRef,
        *,
        path: str = ".",
        recursive: bool = False,
        limit: int = 1000,
    ) -> list[str]:
        ...

    async def apply_patch(self, ws: WorkspaceRef, patch: Patch) -> PatchArtifact:
        ...

    async def run_command(self, ws: WorkspaceRef, spec: CommandSpec) -> CommandResult:
        ...

    def stream_command(
        self,
        ws: WorkspaceRef,
        spec: CommandSpec,
    ) -> AsyncIterator[AgentEvent]:
        ...

    async def snapshot(self, ws: WorkspaceRef, *, name: str | None = None) -> Artifact:
        ...

    async def restore(
        self,
        snapshot: ArtifactRef,
        *,
        spec: WorkspaceSpec | None = None,
    ) -> WorkspaceRef:
        ...

    async def expose_port(
        self,
        ws: WorkspaceRef,
        port: int,
        *,
        protocol: str = "http",
        name: str | None = None,
    ) -> WorkspacePort:
        ...

    async def list_artifacts(
        self,
        ws: WorkspaceRef,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        ...

    async def export_artifact(self, ws: WorkspaceRef, ref: ArtifactRef) -> Artifact:
        ...

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        ...

    def session_state(self, ws: WorkspaceRef) -> WorkspaceSessionState:
        ...

    def drain_events(self) -> list[AgentEvent]:
        ...
