from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from agent_runtime.core.artifacts import Artifact
from agent_runtime.workspaces.spec import WorkspaceRef

ChangeType = Literal["create", "update", "delete", "rename"]


@dataclass(slots=True, frozen=True)
class FileChange:
    path: str
    type: ChangeType
    content: str | None = None
    old_path: str | None = None


@dataclass(slots=True, frozen=True)
class Patch:
    summary: str
    diff: str
    changes: list[FileChange]


@dataclass(slots=True, frozen=True)
class PatchArtifact:
    """A patch produced by a workspace operation.

    Convertible to a generic :class:`Artifact` via :meth:`to_artifact`. The
    canonical artifact ``type`` is ``"patch"`` and the artifact ``data``
    carries the diff text plus the structured per-file changes.
    """

    summary: str
    diff: str
    changes: list[FileChange]
    workspace: WorkspaceRef | None = None
    id: str = field(default_factory=lambda: f"patch_{uuid4().hex}")
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_artifact(self, *, lazy: bool = False) -> Artifact:
        metadata = dict(self.metadata)
        if lazy:
            metadata.update(
                {
                    "lazy": True,
                    "diff_bytes": len(self.diff.encode("utf-8")),
                    "change_count": len(self.changes),
                }
            )
            return Artifact(
                type="patch",
                name=self.summary,
                data=None,
                id=self.id,
                metadata=metadata,
            )
        return Artifact(
            type="patch",
            name=self.summary,
            data={"diff": self.diff, "changes": list(self.changes)},
            id=self.id,
            metadata=metadata,
        )


@dataclass(slots=True, frozen=True)
class CommandSpec:
    """Specification for a command to execute inside a workspace."""

    command: str | list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout: float | None = None
    shell: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def display(self) -> str:
        if isinstance(self.command, list):
            return " ".join(self.command)
        return self.command


@dataclass(slots=True, frozen=True)
class CommandResult:
    """Outcome of executing a :class:`CommandSpec`."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float | None = None
    timed_out: bool = False
    command_id: str | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out
