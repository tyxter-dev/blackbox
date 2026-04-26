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
    @property
    def client_id(self) -> str:
        ...

    async def create_session(self, spec: WorkspaceSpec) -> SandboxSession:
        ...

    async def attach_session(self, state: WorkspaceSessionState) -> SandboxSession:
        ...

    async def close_session(self, session: SandboxSession, *, delete: bool = False) -> None:
        ...

    async def read_file(self, session: SandboxSession, path: str) -> str:
        ...

    async def write_file(self, session: SandboxSession, path: str, content: str) -> None:
        ...

    async def delete_file(self, session: SandboxSession, path: str) -> None:
        ...

    async def list_files(
        self,
        session: SandboxSession,
        *,
        path: str = ".",
        recursive: bool = False,
        limit: int = 1000,
    ) -> list[str]:
        ...

    async def run_command(
        self,
        session: SandboxSession,
        spec: CommandSpec,
    ) -> CommandResult:
        ...

    def stream_command(
        self,
        session: SandboxSession,
        spec: CommandSpec,
    ) -> AsyncIterator[SandboxCommandEvent]:
        ...

    async def snapshot(self, session: SandboxSession, *, name: str | None = None) -> Artifact:
        ...

    async def restore(
        self,
        snapshot: ArtifactRef,
        spec: WorkspaceSpec | None = None,
    ) -> SandboxSession:
        ...

    async def expose_port(
        self,
        session: SandboxSession,
        port: int,
        *,
        protocol: str = "http",
        name: str | None = None,
    ) -> WorkspacePort:
        ...

    def session_state(self, session: SandboxSession) -> WorkspaceSessionState:
        ...
