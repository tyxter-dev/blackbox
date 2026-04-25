"""Local workspace runtime.

Provides file read/write/delete, patch application, command execution, and
snapshotting for ``"local"`` workspaces. Emits canonical workspace events
through ``runtime.events`` (a list buffer drained by callers) and consults
an optional :class:`Policy` at the M2 checkpoints:

- ``before_workspace_write`` — for any mutating file operation,
- ``before_command`` — for command execution,
- ``before_artifact_export`` — for snapshots and patch artifacts.

v0.1 only supports ``WorkspaceSpec.kind == "local"``; other kinds raise
:class:`WorkspaceError`. ``require_approval`` policy verdicts at workspace
checkpoints raise :class:`ApprovalError` because there is no approval
channel wired into this layer yet — integration with the AgentLoop's
approval flow lands in a later slice.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runtime.core.artifacts import Artifact, ArtifactPage
from agent_runtime.core.errors import ApprovalError, WorkspaceError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.policy import Policy, PolicyDecision, PolicyRequest
from agent_runtime.workspaces.changes import (
    ChangeType,
    CommandResult,
    CommandSpec,
    FileChange,
    Patch,
    PatchArtifact,
)
from agent_runtime.workspaces.spec import WorkspaceRef, WorkspaceSpec

CommandExecutor = Callable[[CommandSpec, WorkspaceRef], Awaitable[CommandResult]]

_PROVIDER = "workspace"


@dataclass
class WorkspaceRuntime:
    """In-process workspace backend for local directories."""

    policy: Policy | None = None
    events: list[AgentEvent] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    executor: CommandExecutor | None = None
    _open: dict[str, WorkspaceRef] = field(default_factory=dict)

    async def open(self, spec: WorkspaceSpec) -> WorkspaceRef:
        if spec.kind != "local":
            raise WorkspaceError(
                f"WorkspaceRuntime currently supports only 'local' workspaces, got {spec.kind!r}."
            )
        if not spec.root:
            raise WorkspaceError("Local workspace requires a root path.")
        root = Path(spec.root)
        if not await asyncio.to_thread(_is_directory, root):
            raise WorkspaceError(f"Workspace root does not exist or is not a directory: {root}")
        ref = WorkspaceRef.local(root=root)
        self._open[ref.id] = ref
        return ref

    async def read_file(self, ws: WorkspaceRef, path: str) -> str:
        target = self._resolve(ws, path)
        try:
            content = await asyncio.to_thread(target.read_text)
        except OSError as exc:
            raise WorkspaceError(f"Failed to read {path}: {exc}") from exc
        self._emit(
            EventTypes.WORKSPACE_FILE_READ,
            data={"workspace_id": ws.id, "path": path, "size": len(content)},
        )
        return content

    async def write_file(
        self, ws: WorkspaceRef, path: str, content: str
    ) -> FileChange:
        target = self._resolve(ws, path)
        existed = await asyncio.to_thread(target.exists)
        change_type: ChangeType = "update" if existed else "create"
        await self._policy_gate(
            "before_workspace_write",
            action=path,
            arguments={"workspace_id": ws.id, "type": change_type},
        )
        try:
            await asyncio.to_thread(_write_text, target, content)
        except OSError as exc:
            raise WorkspaceError(f"Failed to write {path}: {exc}") from exc
        change = FileChange(path=path, type=change_type, content=content)
        self._emit(
            EventTypes.WORKSPACE_FILE_CHANGED,
            data={"workspace_id": ws.id, "change": change},
        )
        return change

    async def delete_file(self, ws: WorkspaceRef, path: str) -> FileChange:
        await self._policy_gate(
            "before_workspace_write",
            action=path,
            arguments={"workspace_id": ws.id, "type": "delete"},
        )
        target = self._resolve(ws, path)
        if not await asyncio.to_thread(target.exists):
            raise WorkspaceError(f"Cannot delete missing file: {path}")
        try:
            await asyncio.to_thread(target.unlink)
        except OSError as exc:
            raise WorkspaceError(f"Failed to delete {path}: {exc}") from exc
        change = FileChange(path=path, type="delete")
        self._emit(
            EventTypes.WORKSPACE_FILE_CHANGED,
            data={"workspace_id": ws.id, "change": change},
        )
        return change

    async def apply_patch(self, ws: WorkspaceRef, patch: Patch) -> PatchArtifact:
        applied: list[FileChange] = []
        for change in patch.changes:
            if change.type in {"create", "update"}:
                if change.content is None:
                    raise WorkspaceError(
                        f"Patch change for {change.path} of type {change.type!r} "
                        "requires content."
                    )
                applied.append(await self.write_file(ws, change.path, change.content))
            elif change.type == "delete":
                applied.append(await self.delete_file(ws, change.path))
            else:
                raise WorkspaceError(
                    f"Unsupported patch change type: {change.type!r} for {change.path}."
                )
        artifact = PatchArtifact(
            summary=patch.summary,
            diff=patch.diff,
            changes=applied,
            workspace=ws,
        )
        await self._policy_gate(
            "before_artifact_export",
            action=artifact.id,
            arguments={"workspace_id": ws.id, "artifact_type": "patch"},
        )
        self._emit(
            EventTypes.WORKSPACE_PATCH_CREATED,
            data={"workspace_id": ws.id, "patch_id": artifact.id, "summary": patch.summary},
        )
        as_artifact = artifact.to_artifact()
        self.artifacts.append(as_artifact)
        self._emit(
            EventTypes.ARTIFACT_CREATED,
            data={"artifact": as_artifact},
        )
        return artifact

    async def run_command(
        self, ws: WorkspaceRef, spec: CommandSpec
    ) -> CommandResult:
        await self._policy_gate(
            "before_command",
            action=spec.display,
            arguments={"workspace_id": ws.id, "cwd": spec.cwd},
        )
        self._emit(
            EventTypes.WORKSPACE_COMMAND_STARTED,
            data={"workspace_id": ws.id, "command": spec.display},
        )
        executor = self.executor or _default_executor
        result = await executor(spec, ws)
        self._emit(
            EventTypes.WORKSPACE_COMMAND_COMPLETED,
            data={
                "workspace_id": ws.id,
                "command": spec.display,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_seconds": result.duration_seconds,
            },
        )
        return result

    async def snapshot(self, ws: WorkspaceRef, *, name: str | None = None) -> Artifact:
        await self._policy_gate(
            "before_artifact_export",
            action=name or ws.id,
            arguments={"workspace_id": ws.id, "artifact_type": "workspace_snapshot"},
        )
        artifact = Artifact(
            type="workspace_snapshot",
            name=name or f"snapshot_{ws.id}",
            data=None,
            metadata={"workspace_id": ws.id, "root": ws.root or ""},
        )
        self.artifacts.append(artifact)
        self._emit(EventTypes.ARTIFACT_CREATED, data={"artifact": artifact})
        return artifact

    def list_artifacts(
        self,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        items = [a for a in self.artifacts if type is None or a.type == type]
        if after is not None:
            cut = next((i for i, a in enumerate(items) if a.id == after), None)
            if cut is not None:
                items = items[cut + 1 :]
        page = items[:limit]
        has_more = len(items) > limit
        next_cursor = page[-1].id if has_more and page else None
        return ArtifactPage(items=page, next_cursor=next_cursor, has_more=has_more)

    def drain_events(self) -> list[AgentEvent]:
        events = list(self.events)
        self.events.clear()
        return events

    def _resolve(self, ws: WorkspaceRef, path: str) -> Path:
        if ws.id not in self._open:
            raise WorkspaceError(f"Unknown workspace: {ws.id}")
        if ws.root is None:
            raise WorkspaceError("Workspace has no local root.")
        root = Path(ws.root).resolve()
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError(
                f"Path escapes workspace root: {path}"
            ) from exc
        return candidate

    async def _policy_gate(
        self, checkpoint: str, *, action: str, arguments: dict[str, Any]
    ) -> None:
        if self.policy is None:
            return
        decision: PolicyDecision = await self.policy.check(
            PolicyRequest(
                checkpoint=checkpoint,  # type: ignore[arg-type]
                action=action,
                arguments=arguments,
            )
        )
        if decision.verdict == "allow":
            return
        if decision.verdict == "deny":
            raise WorkspaceError(
                f"Policy denied {checkpoint}: {decision.reason or 'no reason provided'}"
            )
        raise ApprovalError(
            f"Policy required approval at {checkpoint} but no approval channel "
            "is wired into WorkspaceRuntime. Reason: "
            f"{decision.reason or 'unspecified'}"
        )

    def _emit(self, type_: str, *, data: dict[str, Any]) -> None:
        self.events.append(AgentEvent(type=type_, provider=_PROVIDER, data=data))


def _is_directory(path: Path) -> bool:
    return path.exists() and path.is_dir()


def _write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


async def _default_executor(spec: CommandSpec, ws: WorkspaceRef) -> CommandResult:
    cwd = spec.cwd or ws.root
    env = {**os.environ, **spec.env} if spec.env else None
    started = time.monotonic()
    if spec.shell or isinstance(spec.command, str):
        cmd = spec.command if isinstance(spec.command, str) else " ".join(spec.command)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        argv = list(spec.command)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    timed_out = False
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=spec.timeout
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        timed_out = True
        stdout_b = b""
        stderr_b = b"timeout"
    return CommandResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout_b.decode(errors="replace"),
        stderr=stderr_b.decode(errors="replace"),
        duration_seconds=time.monotonic() - started,
        timed_out=timed_out,
    )
