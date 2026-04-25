from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

WorkspaceKind = Literal["local", "git", "sandbox", "cloud"]


@dataclass(slots=True, frozen=True)
class WorkspaceSpec:
    kind: WorkspaceKind
    root: str | None = None
    repo: str | None = None
    branch: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def local(cls, root: str | Path) -> WorkspaceSpec:
        return cls(kind="local", root=str(root))

    @classmethod
    def git(cls, repo: str, *, branch: str | None = None) -> WorkspaceSpec:
        return cls(kind="git", repo=repo, branch=branch)


@dataclass(slots=True, frozen=True)
class WorkspaceRef:
    """Stable handle for an opened workspace.

    Returned by :meth:`WorkspaceRuntime.open`. ``root`` is populated for
    local workspaces; remote/cloud workspaces may leave it ``None`` and rely
    on ``provider`` + ``id`` for identity.
    """

    id: str
    kind: WorkspaceKind
    root: str | None = None
    provider: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def local(cls, root: str | Path, *, id: str | None = None) -> WorkspaceRef:
        return cls(
            id=id or f"ws_{uuid4().hex}",
            kind="local",
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
