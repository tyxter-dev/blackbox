from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from blackbox.core.artifacts import Artifact, ArtifactRef
from blackbox.core.errors import (
    ProviderExecutionError,
    UnsupportedFeatureError,
    WorkspaceError,
)
from blackbox.core.policy import Policy
from blackbox.workspaces.changes import CommandResult, CommandSpec
from blackbox.workspaces.fake import _list_files, _materialize_inputs, _write_text
from blackbox.workspaces.local import _archive_workspace, _extract_workspace_archive
from blackbox.workspaces.sandbox import SandboxWorkspaceProvider
from blackbox.workspaces.sandbox_client import (
    SandboxCommandEvent,
    SandboxSession,
)
from blackbox.workspaces.spec import WorkspacePort, WorkspaceSessionState, WorkspaceSpec


@dataclass(slots=True)
class DockerSandboxClient:
    """SandboxClient implementation that runs workspace commands in Docker containers."""

    image: str
    docker_bin: str = "docker"
    workdir: str = "/workspace"
    network: Literal["none", "default"] = "none"
    remove_on_close: bool = True
    command_output_limit: int = 200_000
    default_timeout: float | None = 120.0
    metadata: dict[str, object] = field(default_factory=dict)
    client_id: str = "docker-sandbox"
    supports_ports: bool = False
    _sessions: dict[str, SandboxSession] = field(default_factory=dict)

    async def create_session(self, spec: WorkspaceSpec) -> SandboxSession:
        host_root = Path(tempfile.mkdtemp(prefix="blackbox-docker-workspace-"))
        await asyncio.to_thread(_materialize_inputs, host_root, spec.inputs)
        container_id = await self._start_container(host_root, spec)
        session = SandboxSession(
            id=container_id,
            root=str(host_root),
            provider_workspace_id=container_id,
            provider_session_id=container_id,
            metadata={
                "container_id": container_id,
                "host_root": str(host_root),
                "workdir": spec.workdir or self.workdir,
                "image": spec.image or self.image,
                "client": self.client_id,
                **self.metadata,
            },
        )
        self._sessions[container_id] = session
        return session

    async def attach_session(self, state: WorkspaceSessionState) -> SandboxSession:
        container_id = state.provider_session_id or state.provider_workspace_id
        if container_id is None:
            raise WorkspaceError("Docker sandbox state is missing a container id.")
        existing = self._sessions.get(container_id)
        if existing is not None:
            return existing
        root_raw = state.serialized_state.get("host_root") or state.serialized_state.get("root")
        if not isinstance(root_raw, str):
            raise WorkspaceError("Docker sandbox state is missing host_root.")
        session = SandboxSession(
            id=container_id,
            root=root_raw,
            provider_workspace_id=state.provider_workspace_id,
            provider_session_id=container_id,
            metadata={**dict(state.metadata), "container_id": container_id, "host_root": root_raw},
        )
        self._sessions[container_id] = session
        return session

    async def close_session(self, session: SandboxSession, *, delete: bool = False) -> None:
        self._sessions.pop(session.id, None)
        if delete or self.remove_on_close:
            await self._docker("rm", "-f", session.id, check=False)
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
        return await asyncio.to_thread(
            _list_files_for_session,
            session.root,
            target,
            recursive,
            limit,
        )

    async def run_command(
        self,
        session: SandboxSession,
        spec: CommandSpec,
    ) -> CommandResult:
        started = time.monotonic()
        timeout = spec.timeout if spec.timeout is not None else self.default_timeout
        workdir = _container_cwd(session, spec.cwd, default_workdir=str(session.metadata["workdir"]))
        env_args: list[str] = []
        for key, value in (spec.env or {}).items():
            env_args.extend(["--env", f"{key}={value}"])
        if spec.shell or isinstance(spec.command, str):
            command = spec.command if isinstance(spec.command, str) else " ".join(spec.command)
            argv = [
                "exec",
                "--workdir",
                workdir,
                *env_args,
                session.id,
                "sh",
                "-lc",
                command,
            ]
        else:
            argv = ["exec", "--workdir", workdir, *env_args, session.id, *spec.command]
        exit_code, stdout, stderr = await self._docker_capture(*argv, command_timeout=timeout)
        return CommandResult(
            exit_code=exit_code,
            stdout=stdout[: self.command_output_limit],
            stderr=stderr[: self.command_output_limit],
            duration_seconds=time.monotonic() - started,
            timed_out=exit_code == -9,
            metadata={"container_id": session.id},
        )

    async def stream_command(
        self,
        session: SandboxSession,
        spec: CommandSpec,
    ) -> AsyncIterator[SandboxCommandEvent]:
        result = await self.run_command(session, spec)
        if result.stdout:
            yield SandboxCommandEvent(stream="stdout", text=result.stdout, command_id=result.command_id)
        if result.stderr:
            yield SandboxCommandEvent(stream="stderr", text=result.stderr, command_id=result.command_id)

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
                "container_id": session.id,
                "root": session.root,
                "format": "tar.gz",
            },
        )

    async def restore(
        self,
        snapshot: ArtifactRef,
        spec: WorkspaceSpec | None = None,
    ) -> SandboxSession:
        if snapshot.uri is None or not snapshot.uri.startswith("file://"):
            raise WorkspaceError(f"Docker sandbox can only restore file snapshots: {snapshot.id}")
        host_root = Path(tempfile.mkdtemp(prefix="blackbox-docker-workspace-"))
        await asyncio.to_thread(
            _extract_workspace_archive,
            Path(snapshot.uri.removeprefix("file://")),
            host_root,
        )
        container_id = await self._start_container(host_root, spec or WorkspaceSpec.sandbox())
        session = SandboxSession(
            id=container_id,
            root=str(host_root),
            provider_workspace_id=container_id,
            provider_session_id=container_id,
            metadata={
                "container_id": container_id,
                "host_root": str(host_root),
                "workdir": (spec.workdir if spec is not None else None) or self.workdir,
                "image": (spec.image if spec is not None else None) or self.image,
                "restored_from": snapshot.id,
                "client": self.client_id,
                **self.metadata,
            },
        )
        self._sessions[container_id] = session
        return session

    async def expose_port(
        self,
        session: SandboxSession,
        port: int,
        *,
        protocol: str = "http",
        name: str | None = None,
    ) -> WorkspacePort:
        raise UnsupportedFeatureError(
            "DockerSandboxClient does not expose ports after session creation in this adapter."
        )

    def session_state(self, session: SandboxSession) -> WorkspaceSessionState:
        return WorkspaceSessionState(
            provider=self.client_id,
            workspace_id=session.id,
            kind="sandbox",
            provider_workspace_id=session.provider_workspace_id,
            provider_session_id=session.provider_session_id,
            root=str(session.metadata.get("workdir") or self.workdir),
            serialized_state={
                "container_id": session.id,
                "host_root": session.root,
                "root": session.root,
                "workdir": session.metadata.get("workdir") or self.workdir,
            },
            metadata=dict(session.metadata),
        )

    async def _start_container(self, host_root: Path, spec: WorkspaceSpec) -> str:
        image = spec.image or self.image
        workdir = spec.workdir or self.workdir
        network = "none" if spec.network == "disabled" else self.network
        args = [
            "run",
            "-d",
            "--network",
            network,
            "--workdir",
            workdir,
            "-v",
            f"{host_root}:{workdir}",
        ]
        for key, value in spec.env.items():
            args.extend(["--env", f"{key}={value}"])
        if spec.user is not None:
            args.extend(["--user", spec.user])
        args.extend([image, "sh", "-lc", "tail -f /dev/null"])
        exit_code, stdout, stderr = await self._docker_capture(
            *args,
            command_timeout=self.default_timeout,
        )
        if exit_code != 0:
            raise ProviderExecutionError(f"Docker session failed to start: {stderr or stdout}")
        container_id = stdout.strip()
        if not container_id:
            raise ProviderExecutionError("Docker did not return a container id.")
        return container_id

    async def _docker(self, *args: str, check: bool = True) -> tuple[int, str, str]:
        exit_code, stdout, stderr = await self._docker_capture(
            *args,
            command_timeout=self.default_timeout,
        )
        if check and exit_code != 0:
            raise ProviderExecutionError(f"Docker command failed: {stderr or stdout}")
        return exit_code, stdout, stderr

    async def _docker_capture(
        self,
        *args: str,
        command_timeout: float | None,
    ) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.docker_bin,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ProviderExecutionError(f"Docker binary not found: {self.docker_bin}") from exc
        timed_out = False
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=command_timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            timed_out = True
            stdout_b = b""
            stderr_b = b"timeout"
        return (
            -9 if timed_out else proc.returncode if proc.returncode is not None else -1,
            stdout_b.decode(errors="replace"),
            stderr_b.decode(errors="replace"),
        )


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


def _list_files_for_session(
    root_raw: str,
    target: Path,
    recursive: bool,
    limit: int,
) -> list[str]:
    return _list_files(Path(root_raw).resolve(), target, recursive, limit)


def _container_cwd(session: SandboxSession, cwd: str | None, *, default_workdir: str) -> str:
    if cwd is None or cwd == ".":
        return default_workdir
    if Path(cwd).is_absolute() or any(part == ".." for part in Path(cwd).parts):
        raise WorkspaceError(f"Path escapes workspace root: {cwd}")
    return f"{default_workdir.rstrip('/')}/{cwd.replace(os.sep, '/')}"


class DockerWorkspaceProvider(SandboxWorkspaceProvider):
    """Docker-backed WorkspaceProvider using ``DockerSandboxClient``."""

    def __init__(
        self,
        *,
        image: str,
        docker_bin: str = "docker",
        workdir: str = "/workspace",
        network: Literal["none", "default"] = "none",
        remove_on_close: bool = True,
        command_output_limit: int = 200_000,
        default_timeout: float | None = 120.0,
        metadata: dict[str, object] | None = None,
        policy: Policy | None = None,
        provider_id: str = "docker-workspace",
    ) -> None:
        client = DockerSandboxClient(
            image=image,
            docker_bin=docker_bin,
            workdir=workdir,
            network=network,
            remove_on_close=remove_on_close,
            command_output_limit=command_output_limit,
            default_timeout=default_timeout,
            metadata=dict(metadata or {}),
        )
        super().__init__(
            client=client,
            policy=policy,
            provider_id=provider_id,
            workspace_kind="docker",
            command_output_limit=command_output_limit,
        )
