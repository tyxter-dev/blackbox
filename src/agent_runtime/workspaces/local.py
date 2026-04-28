"""Local filesystem implementation of the workspace provider protocol."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tarfile
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactPage, ArtifactRef
from agent_runtime.core.errors import UnsupportedFeatureError, WorkspaceError
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
from agent_runtime.workspaces.provider import (
    PendingWorkspaceOperation,
    WorkspaceApprovalRequired,
)
from agent_runtime.workspaces.serialization import workspace_state_to_dict
from agent_runtime.workspaces.spec import (
    WorkspacePort,
    WorkspaceProviderCapabilities,
    WorkspaceRef,
    WorkspaceSessionState,
    WorkspaceSpec,
)

CommandExecutor = Callable[[CommandSpec, WorkspaceRef], Awaitable[CommandResult]]


class AwaitableArtifactPage(ArtifactPage):
    def __await__(self) -> Any:
        async def _self() -> AwaitableArtifactPage:
            return self

        return _self().__await__()


@dataclass
class LocalWorkspaceProvider:
    """In-process workspace provider for local directories."""

    policy: Policy | None = None
    events: list[AgentEvent] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    executor: CommandExecutor | None = None
    provider_id: str = "local-workspace"
    command_output_limit: int = 200_000
    _open: dict[str, WorkspaceRef] = field(default_factory=dict)
    _approved_operations: set[str] = field(default_factory=set)
    _denied_operations: set[str] = field(default_factory=set)

    def capabilities(self) -> WorkspaceProviderCapabilities:
        return WorkspaceProviderCapabilities(
            supports_local_files=True,
            supports_commands=True,
            supports_patches=True,
            supports_snapshots=True,
            supports_restore=True,
            supports_artifacts=True,
            supports_approvals=True,
            supports_resume=True,
        )

    async def open(self, spec: WorkspaceSpec) -> WorkspaceRef:
        if spec.kind != "local":
            raise WorkspaceError(
                f"LocalWorkspaceProvider supports only 'local' workspaces, got {spec.kind!r}."
            )
        if not spec.root:
            raise WorkspaceError("Local workspace requires a root path.")
        await self._policy_gate(
            "before_workspace_open",
            workspace_id=None,
            operation="open",
            action=spec.root,
            arguments={"kind": spec.kind, "root": spec.root},
        )
        root = Path(spec.root)
        if not await asyncio.to_thread(_is_directory, root):
            raise WorkspaceError(f"Workspace root does not exist or is not a directory: {root}")
        resolved_root = await asyncio.to_thread(lambda: str(root.resolve()))
        ref = WorkspaceRef.local(root=resolved_root, provider=self.provider_id)
        state = self.session_state(ref)
        ref = replace(ref, session_state=state)
        self._open[ref.id] = ref
        self._emit(
            EventTypes.WORKSPACE_OPENED,
            ws=ref,
            data={"root": ref.root, "session_state": workspace_state_to_dict(state)},
        )
        return ref

    async def attach(self, state: WorkspaceSessionState) -> WorkspaceRef:
        if state.kind != "local":
            raise WorkspaceError(f"Cannot attach non-local workspace state: {state.kind!r}.")
        if not state.root:
            raise WorkspaceError("Local workspace state requires a root path.")
        root = Path(state.root)
        if not await asyncio.to_thread(_is_directory, root):
            raise WorkspaceError(f"Workspace root does not exist or is not a directory: {root}")
        resolved_root = await asyncio.to_thread(lambda: str(root.resolve()))
        ref = WorkspaceRef(
            id=state.workspace_id,
            kind="local",
            provider=self.provider_id,
            root=resolved_root,
            session_state=state,
            provider_workspace_id=state.provider_workspace_id,
            provider_session_id=state.provider_session_id,
            metadata=dict(state.metadata),
        )
        self._open[ref.id] = ref
        self._emit(
            EventTypes.WORKSPACE_OPENED,
            ws=ref,
            data={"root": ref.root, "attached": True, "session_state": workspace_state_to_dict(state)},
        )
        return ref

    async def close(self, ws: WorkspaceRef, *, delete: bool = False) -> None:
        self._open.pop(ws.id, None)
        if delete and ws.root is not None:
            await asyncio.to_thread(_delete_tree_if_exists, Path(ws.root))
        self._emit(EventTypes.WORKSPACE_CLOSED, ws=ws, data={"delete": delete})

    async def read_file(self, ws: WorkspaceRef, path: str) -> str:
        await self._policy_gate(
            "before_workspace_read",
            workspace_id=ws.id,
            operation="read_file",
            action=path,
            arguments={"workspace_id": ws.id, "path": path},
        )
        target = self._resolve(ws, path)
        try:
            content = await asyncio.to_thread(target.read_text)
        except OSError as exc:
            raise WorkspaceError(f"Failed to read {path}: {exc}") from exc
        self._emit(
            EventTypes.WORKSPACE_FILE_READ,
            ws=ws,
            data={"path": path, "bytes": len(content), "size": len(content)},
        )
        return content

    async def write_file(self, ws: WorkspaceRef, path: str, content: str) -> FileChange:
        target = self._resolve(ws, path)
        existed = await asyncio.to_thread(target.exists)
        change_type: ChangeType = "update" if existed else "create"
        await self._policy_gate(
            "before_workspace_write",
            workspace_id=ws.id,
            operation="write_file",
            action=path,
            arguments={"workspace_id": ws.id, "type": change_type, "path": path},
        )
        try:
            await asyncio.to_thread(_write_text, target, content)
        except OSError as exc:
            raise WorkspaceError(f"Failed to write {path}: {exc}") from exc
        change = FileChange(path=path, type=change_type, content=content)
        self._emit(EventTypes.WORKSPACE_FILE_CHANGED, ws=ws, data={"change": change})
        return change

    async def delete_file(self, ws: WorkspaceRef, path: str) -> FileChange:
        await self._policy_gate(
            "before_workspace_write",
            workspace_id=ws.id,
            operation="delete_file",
            action=path,
            arguments={"workspace_id": ws.id, "type": "delete", "path": path},
        )
        target = self._resolve(ws, path)
        if not await asyncio.to_thread(target.exists):
            raise WorkspaceError(f"Cannot delete missing file: {path}")
        try:
            await asyncio.to_thread(target.unlink)
        except OSError as exc:
            raise WorkspaceError(f"Failed to delete {path}: {exc}") from exc
        change = FileChange(path=path, type="delete")
        self._emit(EventTypes.WORKSPACE_FILE_CHANGED, ws=ws, data={"change": change})
        return change

    async def list_files(
        self,
        ws: WorkspaceRef,
        *,
        path: str = ".",
        recursive: bool = False,
        limit: int = 1000,
    ) -> list[str]:
        target = self._resolve(ws, path)
        if not await asyncio.to_thread(target.exists):
            raise WorkspaceError(f"Cannot list missing path: {path}")
        root = self._root(ws)
        try:
            files = await asyncio.to_thread(_list_files, root, target, recursive, limit)
        except OSError as exc:
            raise WorkspaceError(f"Failed to list {path}: {exc}") from exc
        self._emit(
            EventTypes.WORKSPACE_FILE_LISTED,
            ws=ws,
            data={"path": path, "recursive": recursive, "count": len(files)},
        )
        return files

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
            workspace_id=ws.id,
            operation="apply_patch",
            action=artifact.id,
            arguments={"workspace_id": ws.id, "artifact_type": "patch"},
        )
        self._emit(
            EventTypes.WORKSPACE_PATCH_CREATED,
            ws=ws,
            data={"patch_id": artifact.id, "summary": patch.summary},
        )
        as_artifact = artifact.to_artifact()
        self.artifacts.append(as_artifact)
        self._emit(EventTypes.ARTIFACT_CREATED, ws=ws, data={"artifact": as_artifact})
        return artifact

    async def run_command(self, ws: WorkspaceRef, spec: CommandSpec) -> CommandResult:
        resolved_spec = self._command_spec_for_workspace(ws, spec)
        command_id = f"cmd_{uuid4().hex}"
        await self._policy_gate(
            "before_command",
            workspace_id=ws.id,
            operation="run_command",
            action=spec.display,
            arguments={"workspace_id": ws.id, "cwd": spec.cwd, "command": spec.display},
        )
        self._emit(
            EventTypes.WORKSPACE_COMMAND_STARTED,
            ws=ws,
            data={"command": spec.display, "command_id": command_id},
        )
        executor = self.executor or _default_executor
        result = await executor(resolved_spec, ws)
        if result.command_id is None:
            result = replace(result, command_id=command_id)
        self._emit_command_output(ws, result)
        artifacts = list(result.artifacts)
        extra_artifact = self._command_output_artifact(ws, spec, result)
        if extra_artifact is not None:
            artifacts.append(extra_artifact)
            self.artifacts.append(extra_artifact)
            self._emit(EventTypes.ARTIFACT_CREATED, ws=ws, data={"artifact": extra_artifact})
        if artifacts != result.artifacts:
            result = replace(result, artifacts=artifacts)
        self._emit(
            EventTypes.WORKSPACE_COMMAND_COMPLETED,
            ws=ws,
            data={
                "command": spec.display,
                "command_id": result.command_id,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_seconds": result.duration_seconds,
            },
        )
        return result

    async def stream_command(
        self,
        ws: WorkspaceRef,
        spec: CommandSpec,
    ) -> AsyncIterator[AgentEvent]:
        await self.run_command(ws, spec)
        for event in self.drain_events():
            yield event

    async def snapshot(self, ws: WorkspaceRef, *, name: str | None = None) -> Artifact:
        await self._policy_gate(
            "before_artifact_export",
            workspace_id=ws.id,
            operation="snapshot",
            action=name or ws.id,
            arguments={"workspace_id": ws.id, "artifact_type": "workspace_snapshot"},
        )
        root = self._root(ws)
        archive_path = await asyncio.to_thread(_archive_workspace, root, name or f"snapshot_{ws.id}")
        artifact = Artifact(
            type="workspace_snapshot",
            name=name or f"snapshot_{ws.id}.tar.gz",
            uri=f"file://{archive_path}",
            metadata={
                "workspace_id": ws.id,
                "provider": self.provider_id,
                "root": ws.root,
                "format": "tar.gz",
            },
        )
        self.artifacts.append(artifact)
        self._emit(
            EventTypes.WORKSPACE_SNAPSHOT_CREATED,
            ws=ws,
            data={"artifact": artifact, "snapshot_id": artifact.id},
        )
        self._emit(EventTypes.ARTIFACT_CREATED, ws=ws, data={"artifact": artifact})
        return artifact

    async def restore(
        self,
        snapshot: ArtifactRef,
        *,
        spec: WorkspaceSpec | None = None,
    ) -> WorkspaceRef:
        await self._policy_gate(
            "before_workspace_restore",
            workspace_id=None,
            operation="restore",
            action=snapshot.id,
            arguments={"snapshot_id": snapshot.id},
        )
        artifact = self._artifact_for_ref(snapshot)
        uri = snapshot.uri or artifact.uri
        if uri is None or not uri.startswith("file://"):
            raise WorkspaceError(f"Cannot restore local workspace from non-file snapshot: {snapshot.id}")
        archive = Path(uri.removeprefix("file://"))
        if not await asyncio.to_thread(archive.exists):
            raise WorkspaceError(f"Snapshot archive does not exist: {archive}")
        root = (
            Path(spec.root)
            if spec is not None and spec.root is not None
            else Path(tempfile.mkdtemp(prefix="agent-runtime-ws-"))
        )
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(_extract_workspace_archive, archive, root)
        ref = WorkspaceRef.local(root=root, provider=self.provider_id)
        state = self.session_state(ref)
        ref = replace(ref, session_state=state, snapshot=snapshot)
        self._open[ref.id] = ref
        self._emit(
            EventTypes.WORKSPACE_SNAPSHOT_RESTORED,
            ws=ref,
            data={"snapshot_id": snapshot.id, "root": str(root)},
        )
        return ref

    async def expose_port(
        self,
        ws: WorkspaceRef,
        port: int,
        *,
        protocol: str = "http",
        name: str | None = None,
    ) -> WorkspacePort:
        raise UnsupportedFeatureError("LocalWorkspaceProvider does not support port exposure.")

    def list_artifacts(
        self,
        ws: WorkspaceRef | None = None,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> AwaitableArtifactPage:
        if ws is None:
            items = [a for a in self.artifacts if type is None or a.type == type]
        else:
            items = [
                a
                for a in self.artifacts
                if (type is None or a.type == type)
                and a.metadata.get("workspace_id") in {None, ws.id}
            ]
        if after is not None:
            cut = next((i for i, a in enumerate(items) if a.id == after), None)
            if cut is not None:
                items = items[cut + 1 :]
        page = items[:limit]
        has_more = len(items) > limit
        next_cursor = page[-1].id if has_more and page else None
        return AwaitableArtifactPage(items=page, next_cursor=next_cursor, has_more=has_more)

    async def export_artifact(self, ws: WorkspaceRef, ref: ArtifactRef) -> Artifact:
        artifact = self._artifact_for_ref(ref)
        self._emit(EventTypes.WORKSPACE_ARTIFACT_EXPORTED, ws=ws, data={"artifact": artifact})
        return artifact

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        if decision.approved:
            self._approved_operations.add(approval_id)
            self._denied_operations.discard(approval_id)
        else:
            self._denied_operations.add(approval_id)
            self._approved_operations.discard(approval_id)

    def session_state(self, ws: WorkspaceRef) -> WorkspaceSessionState:
        if ws.root is None:
            raise WorkspaceError("Local workspace has no root to serialize.")
        return WorkspaceSessionState(
            provider=self.provider_id,
            workspace_id=ws.id,
            kind="local",
            provider_workspace_id=ws.provider_workspace_id,
            provider_session_id=ws.provider_session_id,
            root=ws.root,
            metadata=dict(ws.metadata),
        )

    def drain_events(self) -> list[AgentEvent]:
        events = list(self.events)
        self.events.clear()
        return events

    def _root(self, ws: WorkspaceRef) -> Path:
        if ws.id not in self._open:
            raise WorkspaceError(f"Unknown workspace: {ws.id}")
        if ws.root is None:
            raise WorkspaceError("Workspace has no local root.")
        return Path(ws.root).resolve()

    def _resolve(self, ws: WorkspaceRef, path: str) -> Path:
        root = self._root(ws)
        if _is_absolute_or_escape(path):
            raise WorkspaceError(f"Path escapes workspace root: {path}")
        candidate = (root / path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError(f"Path escapes workspace root: {path}") from exc
        return candidate

    def _command_spec_for_workspace(self, ws: WorkspaceRef, spec: CommandSpec) -> CommandSpec:
        cwd = spec.cwd
        if cwd is None:
            return replace(spec, cwd=str(self._root(ws)))
        resolved = self._resolve(ws, cwd)
        return replace(spec, cwd=str(resolved))

    async def _policy_gate(
        self,
        checkpoint: str,
        *,
        workspace_id: str | None,
        operation: str,
        action: str,
        arguments: dict[str, Any],
    ) -> None:
        """Evaluate policy and one-shot approval overrides for a local workspace operation."""
        approval_id = _approval_id(self.provider_id, workspace_id, checkpoint, action, arguments)
        if approval_id in self._approved_operations:
            self._approved_operations.remove(approval_id)
            return
        if approval_id in self._denied_operations:
            self._denied_operations.remove(approval_id)
            raise WorkspaceError(f"Workspace operation denied by approval: {operation}")
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
        pending = PendingWorkspaceOperation(
            id=approval_id,
            workspace_id=workspace_id or "",
            provider=self.provider_id,
            operation=operation,
            arguments=dict(arguments),
            reason=decision.reason,
            metadata={"checkpoint": checkpoint},
        )
        raise WorkspaceApprovalRequired(pending, approve=self.approve)

    def _emit(self, type_: str, *, ws: WorkspaceRef, data: dict[str, Any]) -> None:
        payload = {
            "workspace_id": ws.id,
            "workspace_kind": ws.kind,
            "workspace_provider": self.provider_id,
            **data,
        }
        self.events.append(AgentEvent(type=type_, provider=self.provider_id, data=payload))

    def _emit_command_output(self, ws: WorkspaceRef, result: CommandResult) -> None:
        command_id = result.command_id
        for stream, text in (("stdout", result.stdout), ("stderr", result.stderr)):
            if not text:
                continue
            self._emit(
                EventTypes.WORKSPACE_COMMAND_OUTPUT,
                ws=ws,
                data={
                    "command_id": command_id,
                    "stream": stream,
                    "text": text[: self.command_output_limit],
                    "truncated": len(text) > self.command_output_limit,
                },
            )

    def _command_output_artifact(
        self,
        ws: WorkspaceRef,
        spec: CommandSpec,
        result: CommandResult,
    ) -> Artifact | None:
        combined = result.stdout + result.stderr
        if not combined:
            return None
        if len(combined) <= self.command_output_limit and spec.metadata.get("artifact") is not True:
            return None
        return Artifact(
            type="command_output",
            name=f"command_output_{result.command_id or uuid4().hex}.log",
            data={"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.exit_code},
            metadata={
                "workspace_id": ws.id,
                "provider": self.provider_id,
                "command": spec.display,
                "command_id": result.command_id,
            },
        )

    def _artifact_for_ref(self, ref: ArtifactRef) -> Artifact:
        for artifact in self.artifacts:
            if artifact.id == ref.id or (ref.uri is not None and artifact.uri == ref.uri):
                return artifact
        if ref.uri is not None:
            return Artifact(
                id=ref.id,
                type="provider_ref",
                name=ref.id,
                uri=ref.uri,
                metadata={"provider": ref.provider or self.provider_id},
            )
        raise WorkspaceError(f"Unknown artifact: {ref.id}")


class WorkspaceRuntime(LocalWorkspaceProvider):
    """Backward-compatible name for the local workspace provider."""


def _is_directory(path: Path) -> bool:
    return path.exists() and path.is_dir()


def _write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _delete_tree(root: Path) -> None:
    import shutil

    shutil.rmtree(root)


def _delete_tree_if_exists(root: Path) -> None:
    resolved = root.resolve()
    if resolved.exists() and resolved.is_dir():
        _delete_tree(resolved)


def _is_absolute_or_escape(path: str) -> bool:
    candidate = Path(path)
    return candidate.is_absolute() or any(part == ".." for part in candidate.parts)


def _list_files(root: Path, target: Path, recursive: bool, limit: int) -> list[str]:
    iterator = target.rglob("*") if recursive else target.iterdir()
    files: list[str] = []
    for entry in iterator:
        if not entry.is_file():
            continue
        files.append(entry.resolve().relative_to(root).as_posix())
        if len(files) >= limit:
            break
    return files


def _safe_archive_stem(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name)
    return safe.removesuffix(".tar.gz") or "snapshot"


def _archive_workspace(root: Path, name: str) -> str:
    snapshots_dir = Path(tempfile.mkdtemp(prefix="agent-runtime-snapshot-"))
    archive_path = snapshots_dir / f"{_safe_archive_stem(name)}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        for entry in root.rglob("*"):
            tar.add(entry, arcname=entry.relative_to(root))
    return str(archive_path)


def _extract_workspace_archive(archive: Path, root: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (root / member.name).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError as exc:
                raise WorkspaceError(f"Snapshot contains path outside workspace: {member.name}") from exc
        tar.extractall(root, filter="data")


def _approval_id(
    provider_id: str,
    workspace_id: str | None,
    checkpoint: str,
    action: str,
    arguments: dict[str, Any],
) -> str:
    body = json.dumps(
        {
            "provider": provider_id,
            "workspace_id": workspace_id,
            "checkpoint": checkpoint,
            "action": action,
            "arguments": arguments,
        },
        sort_keys=True,
        default=repr,
    )
    return f"workspace_approval_{hashlib.sha256(body.encode()).hexdigest()[:24]}"


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
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=spec.timeout)
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
