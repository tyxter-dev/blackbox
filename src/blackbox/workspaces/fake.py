from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from blackbox.core.artifacts import Artifact, ArtifactRef
from blackbox.core.errors import WorkspaceError
from blackbox.workspaces.changes import CommandResult, CommandSpec
from blackbox.workspaces.local import _archive_workspace, _extract_workspace_archive
from blackbox.workspaces.sandbox_client import (
    SandboxCommandEvent,
    SandboxSession,
)
from blackbox.workspaces.spec import WorkspacePort, WorkspaceSessionState, WorkspaceSpec


@dataclass
class FakeSandboxClient:
    """Deterministic local sandbox client for tests.

    It stores each sandbox session in a temp directory and optionally consumes
    queued command results before falling back to local subprocess execution.
    """

    scripted_results: list[CommandResult] = field(default_factory=list)
    client_id: str = "fake-sandbox"
    _sessions: dict[str, SandboxSession] = field(default_factory=dict)

    async def create_session(self, spec: WorkspaceSpec) -> SandboxSession:
        root = Path(tempfile.mkdtemp(prefix="blackbox-fake-sandbox-"))
        await asyncio.to_thread(_materialize_inputs, root, spec.inputs)
        session_id = f"sandbox_{uuid4().hex}"
        session = SandboxSession(
            id=session_id,
            root=str(root),
            provider_workspace_id=session_id,
            provider_session_id=session_id,
            metadata={
                "env": dict(spec.env),
                "image": spec.image,
                "root": spec.root,
                "client": self.client_id,
            },
        )
        self._sessions[session_id] = session
        return session

    async def attach_session(self, state: WorkspaceSessionState) -> SandboxSession:
        session_id = state.provider_session_id or state.provider_workspace_id or state.workspace_id
        session = self._sessions.get(session_id)
        if session is not None:
            return session
        root_raw = state.serialized_state.get("root") or state.root
        if not isinstance(root_raw, str):
            raise WorkspaceError("Fake sandbox state is missing a root path.")
        root = Path(root_raw)
        if not await asyncio.to_thread(_is_directory, root):
            raise WorkspaceError(f"Fake sandbox root does not exist: {root}")
        session = SandboxSession(
            id=session_id,
            root=str(root),
            provider_workspace_id=state.provider_workspace_id,
            provider_session_id=state.provider_session_id,
            metadata=dict(state.metadata),
        )
        self._sessions[session_id] = session
        return session

    async def close_session(self, session: SandboxSession, *, delete: bool = False) -> None:
        self._sessions.pop(session.id, None)
        if delete:
            await asyncio.to_thread(shutil.rmtree, session.root, True)

    async def read_file(self, session: SandboxSession, path: str) -> str:
        target = _resolve(session, path)
        try:
            return await asyncio.to_thread(target.read_text)
        except OSError as exc:
            raise WorkspaceError(f"Failed to read {path}: {exc}") from exc

    async def write_file(self, session: SandboxSession, path: str, content: str) -> None:
        target = _resolve(session, path)
        try:
            await asyncio.to_thread(_write_text, target, content)
        except OSError as exc:
            raise WorkspaceError(f"Failed to write {path}: {exc}") from exc

    async def delete_file(self, session: SandboxSession, path: str) -> None:
        target = _resolve(session, path)
        if not await asyncio.to_thread(target.exists):
            raise WorkspaceError(f"Cannot delete missing file: {path}")
        try:
            await asyncio.to_thread(target.unlink)
        except OSError as exc:
            raise WorkspaceError(f"Failed to delete {path}: {exc}") from exc

    async def list_files(
        self,
        session: SandboxSession,
        *,
        path: str = ".",
        recursive: bool = False,
        limit: int = 1000,
    ) -> list[str]:
        target = _resolve(session, path)
        return await asyncio.to_thread(_list_files_for_session, session.root, target, recursive, limit)

    async def run_command(self, session: SandboxSession, spec: CommandSpec) -> CommandResult:
        command_id = f"cmd_{uuid4().hex}"
        if self.scripted_results:
            result = self.scripted_results.pop(0)
            return replace(result, command_id=result.command_id or command_id)
        return await _run_local_command(session, spec, command_id=command_id)

    async def stream_command(
        self,
        session: SandboxSession,
        spec: CommandSpec,
    ) -> AsyncIterator[SandboxCommandEvent]:
        result = await self.run_command(session, spec)
        if result.stdout:
            yield SandboxCommandEvent("stdout", result.stdout, command_id=result.command_id)
        if result.stderr:
            yield SandboxCommandEvent("stderr", result.stderr, command_id=result.command_id)

    async def snapshot(self, session: SandboxSession, *, name: str | None = None) -> Artifact:
        archive_path = await asyncio.to_thread(
            _archive_workspace,
            Path(session.root),
            name or f"snapshot_{session.id}",
        )
        return Artifact(
            type="workspace_snapshot",
            name=name or f"snapshot_{session.id}.tar.gz",
            uri=f"file://{archive_path}",
            metadata={
                "provider": self.client_id,
                "provider_session_id": session.provider_session_id,
                "root": session.root,
                "format": "tar.gz",
            },
        )

    async def restore(
        self,
        snapshot: ArtifactRef,
        spec: WorkspaceSpec | None = None,
    ) -> SandboxSession:
        uri = snapshot.uri
        if uri is None or not uri.startswith("file://"):
            raise WorkspaceError(f"Fake sandbox can only restore file snapshots: {snapshot.id}")
        root = Path(tempfile.mkdtemp(prefix="blackbox-fake-sandbox-"))
        await asyncio.to_thread(_extract_workspace_archive, Path(uri.removeprefix("file://")), root)
        session_id = f"sandbox_{uuid4().hex}"
        session = SandboxSession(
            id=session_id,
            root=str(root),
            provider_workspace_id=session_id,
            provider_session_id=session_id,
            metadata={"restored_from": snapshot.id, "client": self.client_id},
        )
        self._sessions[session_id] = session
        return session

    async def expose_port(
        self,
        session: SandboxSession,
        port: int,
        *,
        protocol: str = "http",
        name: str | None = None,
    ) -> WorkspacePort:
        return WorkspacePort(
            workspace_id=session.id,
            port=port,
            protocol=protocol,  # type: ignore[arg-type]
            host="127.0.0.1",
            url=f"{protocol}://127.0.0.1:{port}",
            name=name,
            metadata={"provider_session_id": session.provider_session_id, "client": self.client_id},
        )

    def session_state(self, session: SandboxSession) -> WorkspaceSessionState:
        return WorkspaceSessionState(
            provider=self.client_id,
            workspace_id=session.id,
            kind="sandbox",
            provider_workspace_id=session.provider_workspace_id,
            provider_session_id=session.provider_session_id,
            root=session.metadata.get("root") if isinstance(session.metadata.get("root"), str) else None,
            serialized_state={"root": session.root},
            metadata=dict(session.metadata),
        )


def _materialize_inputs(root: Path, inputs: dict[str, str]) -> None:
    for name, source_raw in inputs.items():
        source = Path(source_raw)
        if not source.exists():
            raise WorkspaceError(f"Sandbox input does not exist: {source}")
        target = root / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _resolve(session: SandboxSession, path: str) -> Path:
    if Path(path).is_absolute() or any(part == ".." for part in Path(path).parts):
        raise WorkspaceError(f"Path escapes workspace root: {path}")
    root = Path(session.root).resolve()
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspaceError(f"Path escapes workspace root: {path}") from exc
    return candidate


def _write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _is_directory(path: Path) -> bool:
    return path.exists() and path.is_dir()


def _list_files_for_session(
    root_raw: str,
    target: Path,
    recursive: bool,
    limit: int,
) -> list[str]:
    return _list_files(Path(root_raw).resolve(), target, recursive, limit)


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


async def _run_local_command(
    session: SandboxSession,
    spec: CommandSpec,
    *,
    command_id: str,
) -> CommandResult:
    cwd = str(_resolve(session, spec.cwd or "."))
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
        proc = await asyncio.create_subprocess_exec(
            *spec.command,
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
        command_id=command_id,
    )
