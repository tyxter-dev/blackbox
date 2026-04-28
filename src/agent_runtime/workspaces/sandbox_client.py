from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.artifacts import Artifact, ArtifactRef
from agent_runtime.workspaces.changes import CommandResult, CommandSpec
from agent_runtime.workspaces.spec import WorkspacePort, WorkspaceSessionState, WorkspaceSpec


@dataclass(slots=True, frozen=True)
class SandboxSession:
    id: str
    root: str
    provider_workspace_id: str | None = None
    provider_session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SandboxCommandEvent:
    stream: str
    text: str
    command_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SandboxClient(Protocol):
    """Protocol for sandbox backends used by SandboxWorkspaceProvider."""

    @property
    def client_id(self) -> str:
        ...

    async def create_session(self, spec: WorkspaceSpec) -> SandboxSession:
        ...

    async def attach_session(self, state: WorkspaceSessionState) -> SandboxSession:
        ...

    async def close_session(self, session: SandboxSession, *, delete: bool = False) -> None:
        """Close a sandbox session and optionally delete provider-owned state."""
        ...

    async def read_file(self, session: SandboxSession, path: str) -> str:
        """Return text content from a sandbox-relative file path."""
        ...

    async def write_file(self, session: SandboxSession, path: str, content: str) -> None:
        """Write text content to a sandbox-relative file path."""
        ...

    async def delete_file(self, session: SandboxSession, path: str) -> None:
        """Delete a sandbox-relative file path."""
        ...

    async def list_files(
        self,
        session: SandboxSession,
        *,
        path: str = ".",
        recursive: bool = False,
        limit: int = 1000,
    ) -> list[str]:
        """List sandbox-relative file paths under a directory."""
        ...

    async def run_command(
        self,
        session: SandboxSession,
        spec: CommandSpec,
    ) -> CommandResult:
        """Run a command inside the sandbox session and return its completed result."""
        ...

    def stream_command(
        self,
        session: SandboxSession,
        spec: CommandSpec,
    ) -> AsyncIterator[SandboxCommandEvent]:
        """Run a command inside the sandbox session and stream output events."""
        ...

    async def snapshot(self, session: SandboxSession, *, name: str | None = None) -> Artifact:
        """Create an artifact that captures the sandbox session state."""
        ...

    async def restore(
        self,
        snapshot: ArtifactRef,
        spec: WorkspaceSpec | None = None,
    ) -> SandboxSession:
        """Restore a sandbox session from a snapshot artifact."""
        ...

    async def expose_port(
        self,
        session: SandboxSession,
        port: int,
        *,
        protocol: str = "http",
        name: str | None = None,
    ) -> WorkspacePort:
        """Expose a sandbox network port and return the provider-published endpoint."""
        ...

    def session_state(self, session: SandboxSession) -> WorkspaceSessionState:
        ...
