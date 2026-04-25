from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

WorkspaceKind = Literal["local", "git", "sandbox", "cloud"]


@dataclass(slots=True, frozen=True)
class WorkspaceSpec:
    kind: WorkspaceKind
    root: str | None = None
    repo: str | None = None
    branch: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def local(cls, root: str | Path) -> "WorkspaceSpec":
        return cls(kind="local", root=str(root))

    @classmethod
    def git(cls, repo: str, *, branch: str | None = None) -> "WorkspaceSpec":
        return cls(kind="git", repo=repo, branch=branch)
