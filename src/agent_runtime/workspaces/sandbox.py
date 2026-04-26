from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactRef
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
from agent_runtime.workspaces.local import AwaitableArtifactPage
from agent_runtime.workspaces.provider import (
    PendingWorkspaceOperation,
    WorkspaceApprovalRequired,
)
from agent_runtime.workspaces.sandbox_client import SandboxClient, SandboxSession
from agent_runtime.workspaces.serialization import workspace_state_to_dict
from agent_runtime.workspaces.spec import (
    WorkspacePort,
    WorkspaceProviderCapabilities,
    WorkspaceRef,
    WorkspaceSessionState,
    WorkspaceSpec,
)


@dataclass(slots=True)
class SandboxWorkspaceProvider:
    client: SandboxClient
    policy: Policy | None = None
    provider_id: str = "sandbox-workspace"
    events: list[AgentEvent] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    command_output_limit: int = 200_000
    _sessions: dict[str, SandboxSession] = field(default_factory=dict)
    _approved_operations: set[str] = field(default_factory=set)
    _denied_operations: set[str] = field(default_factory=set)

    def capabilities(self) -> WorkspaceProviderCapabilities:
        supports_ports = bool(getattr(self.client, "supports_ports", True))
        return WorkspaceProviderCapabilities(
            supports_sandbox=True,
            supports_commands=True,
            supports_streaming_command_output=True,
            supports_patches=True,
            supports_snapshots=True,
            supports_restore=True,
            supports_artifacts=True,
            supports_ports=supports_ports,
            supports_approvals=True,
            supports_resume=True,
        )

    async def open(self, spec: WorkspaceSpec) -> WorkspaceRef:
        if spec.kind != "sandbox":
            raise UnsupportedFeatureError(
                f"SandboxWorkspaceProvider supports only 'sandbox' workspaces, got {spec.kind!r}."
            )
        await self._policy_gate(
            "before_workspace_open",
            workspace_id=None,
            operation="open",
            action=spec.root or "/workspace",
            arguments={"kind": spec.kind, "root": spec.root, "image": spec.image},
        )
        if spec.session_state is not None:
            session = await self.client.attach_session(spec.session_state)
        elif spec.snapshot is not None:
            session = await self.client.restore(spec.snapshot, spec)
        else:
            session = await self.client.create_session(spec)
        ref = self._ref_from_session(session, root=spec.root or session.root)
        self._sessions[ref.id] = session
        state = self.session_state(ref)
        ref = replace(ref, session_state=state)
        self._sessions[ref.id] = session
        self._emit(
            EventTypes.WORKSPACE_OPENED,
            ws=ref,
            data={"session_state": workspace_state_to_dict(state)},
        )
        return ref

    async def attach(self, state: WorkspaceSessionState) -> WorkspaceRef:
        if state.kind != "sandbox":
            raise WorkspaceError(f"Cannot attach non-sandbox workspace state: {state.kind!r}.")
        session = await self.client.attach_session(state)
        ref = WorkspaceRef(
            id=state.workspace_id,
            kind="sandbox",
            provider=self.provider_id,
            root=state.root or session.root,
            provider_workspace_id=state.provider_workspace_id,
            provider_session_id=state.provider_session_id,
            session_state=state,
            metadata=dict(state.metadata),
        )
        self._sessions[ref.id] = session
        self._emit(
            EventTypes.WORKSPACE_OPENED,
            ws=ref,
            data={"attached": True, "session_state": workspace_state_to_dict(state)},
        )
        return ref

    async def close(self, ws: WorkspaceRef, *, delete: bool = False) -> None:
        session = self._sessions.pop(ws.id, None)
        if session is not None:
            await self.client.close_session(session, delete=delete)
        self._emit(EventTypes.WORKSPACE_CLOSED, ws=ws, data={"delete": delete})

    async def read_file(self, ws: WorkspaceRef, path: str) -> str:
        _validate_relative_path(path)
        await self._policy_gate(
            "before_workspace_read",
            workspace_id=ws.id,
            operation="read_file",
            action=path,
            arguments={"workspace_id": ws.id, "path": path},
        )
        content = await self.client.read_file(self._session(ws), path)
        self._emit(
            EventTypes.WORKSPACE_FILE_READ,
            ws=ws,
            data={"path": path, "bytes": len(content), "size": len(content)},
        )
        return content

    async def write_file(self, ws: WorkspaceRef, path: str, content: str) -> FileChange:
        _validate_relative_path(path)
        exists = True
        try:
            await self.client.read_file(self._session(ws), path)
        except WorkspaceError:
            exists = False
        change_type: ChangeType = "update" if exists else "create"
        await self._policy_gate(
            "before_workspace_write",
            workspace_id=ws.id,
            operation="write_file",
            action=path,
            arguments={"workspace_id": ws.id, "type": change_type, "path": path},
        )
        await self.client.write_file(self._session(ws), path, content)
        change = FileChange(path=path, type=change_type, content=content)
        self._emit(EventTypes.WORKSPACE_FILE_CHANGED, ws=ws, data={"change": change})
        return change

    async def delete_file(self, ws: WorkspaceRef, path: str) -> FileChange:
        _validate_relative_path(path)
        await self._policy_gate(
            "before_workspace_write",
            workspace_id=ws.id,
            operation="delete_file",
            action=path,
            arguments={"workspace_id": ws.id, "type": "delete", "path": path},
        )
        await self.client.delete_file(self._session(ws), path)
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
        _validate_relative_path(path)
        files = await self.client.list_files(
            self._session(ws),
            path=path,
            recursive=recursive,
            limit=limit,
        )
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
                        f"Patch change for {change.path} of type {change.type!r} requires content."
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
        command_id = f"cmd_{uuid4().hex}"
        _validate_relative_path(spec.cwd or ".")
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
        result = await self.client.run_command(self._session(ws), spec)
        if result.command_id is None:
            result = replace(result, command_id=command_id)
        self._emit_command_output(ws, result)
        for artifact in result.artifacts:
            self.artifacts.append(artifact)
            self._emit(EventTypes.ARTIFACT_CREATED, ws=ws, data={"artifact": artifact})
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
        command_id = f"cmd_{uuid4().hex}"
        _validate_relative_path(spec.cwd or ".")
        await self._policy_gate(
            "before_command",
            workspace_id=ws.id,
            operation="stream_command",
            action=spec.display,
            arguments={"workspace_id": ws.id, "cwd": spec.cwd, "command": spec.display},
        )
        started = self._event(
            EventTypes.WORKSPACE_COMMAND_STARTED,
            ws=ws,
            data={"command": spec.display, "command_id": command_id},
        )
        self.events.append(started)
        yield started
        async for output in self.client.stream_command(self._session(ws), spec):
            event = self._event(
                EventTypes.WORKSPACE_COMMAND_OUTPUT,
                ws=ws,
                data={
                    "command_id": output.command_id or command_id,
                    "stream": output.stream,
                    "text": output.text[: self.command_output_limit],
                    "truncated": len(output.text) > self.command_output_limit,
                },
                raw=output.metadata,
            )
            self.events.append(event)
            yield event

    async def snapshot(self, ws: WorkspaceRef, *, name: str | None = None) -> Artifact:
        await self._policy_gate(
            "before_artifact_export",
            workspace_id=ws.id,
            operation="snapshot",
            action=name or ws.id,
            arguments={"workspace_id": ws.id, "artifact_type": "workspace_snapshot"},
        )
        artifact = await self.client.snapshot(self._session(ws), name=name)
        artifact = _with_workspace_metadata(artifact, ws, self.provider_id)
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
        session = await self.client.restore(snapshot, spec)
        ref = self._ref_from_session(session, root=(spec.root if spec is not None else None) or session.root)
        self._sessions[ref.id] = session
        state = self.session_state(ref)
        ref = replace(ref, session_state=state, snapshot=snapshot)
        self._sessions[ref.id] = session
        self._emit(
            EventTypes.WORKSPACE_SNAPSHOT_RESTORED,
            ws=ref,
            data={"snapshot_id": snapshot.id, "session_state": workspace_state_to_dict(state)},
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
        if not self.capabilities().supports_ports:
            raise UnsupportedFeatureError("Sandbox client does not support port exposure.")
        await self._policy_gate(
            "before_port_expose",
            workspace_id=ws.id,
            operation="expose_port",
            action=str(port),
            arguments={"workspace_id": ws.id, "port": port, "protocol": protocol},
        )
        exposed = await self.client.expose_port(
            self._session(ws),
            port,
            protocol=protocol,
            name=name,
        )
        if exposed.workspace_id != ws.id:
            exposed = replace(exposed, workspace_id=ws.id)
        artifact = Artifact(
            type="port",
            name=name or f"{protocol}:{port}",
            uri=exposed.url,
            metadata={
                "workspace_id": ws.id,
                "provider": self.provider_id,
                "port": port,
                "protocol": protocol,
            },
        )
        self.artifacts.append(artifact)
        self._emit(EventTypes.WORKSPACE_PORT_EXPOSED, ws=ws, data={"port": exposed})
        self._emit(EventTypes.ARTIFACT_CREATED, ws=ws, data={"artifact": artifact})
        return exposed

    def list_artifacts(
        self,
        ws: WorkspaceRef | None = None,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> AwaitableArtifactPage:
        items = [
            artifact
            for artifact in self.artifacts
            if (type is None or artifact.type == type)
            and (ws is None or artifact.metadata.get("workspace_id") in {None, ws.id})
        ]
        if after is not None:
            cut = next((i for i, artifact in enumerate(items) if artifact.id == after), None)
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
        session = self._session(ws)
        client_state = self.client.session_state(session)
        return WorkspaceSessionState(
            provider=self.provider_id,
            workspace_id=ws.id,
            kind="sandbox",
            provider_workspace_id=client_state.provider_workspace_id or session.provider_workspace_id,
            provider_session_id=client_state.provider_session_id or session.provider_session_id,
            root=ws.root,
            snapshot_id=client_state.snapshot_id,
            serialized_state=dict(client_state.serialized_state),
            metadata={
                **dict(client_state.metadata),
                "client": getattr(self.client, "client_id", None),
            },
        )

    def drain_events(self) -> list[AgentEvent]:
        events = list(self.events)
        self.events.clear()
        return events

    def _session(self, ws: WorkspaceRef) -> SandboxSession:
        try:
            return self._sessions[ws.id]
        except KeyError as exc:
            raise WorkspaceError(f"Unknown workspace: {ws.id}") from exc

    def _ref_from_session(self, session: SandboxSession, *, root: str | None) -> WorkspaceRef:
        return WorkspaceRef(
            kind="sandbox",
            provider=self.provider_id,
            root=root,
            provider_workspace_id=session.provider_workspace_id,
            provider_session_id=session.provider_session_id or session.id,
            metadata={"client": getattr(self.client, "client_id", None), **dict(session.metadata)},
        )

    async def _policy_gate(
        self,
        checkpoint: str,
        *,
        workspace_id: str | None,
        operation: str,
        action: str,
        arguments: dict[str, Any],
    ) -> None:
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

    def _event(
        self,
        type_: str,
        *,
        ws: WorkspaceRef,
        data: dict[str, Any],
        raw: Any | None = None,
    ) -> AgentEvent:
        return AgentEvent(
            type=type_,
            provider=self.provider_id,
            raw=raw,
            data={
                "workspace_id": ws.id,
                "workspace_kind": ws.kind,
                "workspace_provider": self.provider_id,
                **data,
            },
        )

    def _emit(self, type_: str, *, ws: WorkspaceRef, data: dict[str, Any]) -> None:
        self.events.append(self._event(type_, ws=ws, data=data))

    def _emit_command_output(self, ws: WorkspaceRef, result: CommandResult) -> None:
        for stream, text in (("stdout", result.stdout), ("stderr", result.stderr)):
            if not text:
                continue
            self._emit(
                EventTypes.WORKSPACE_COMMAND_OUTPUT,
                ws=ws,
                data={
                    "command_id": result.command_id,
                    "stream": stream,
                    "text": text[: self.command_output_limit],
                    "truncated": len(text) > self.command_output_limit,
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


def _validate_relative_path(path: str) -> None:
    candidate = Path(path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise WorkspaceError(f"Path escapes workspace root: {path}")


def _with_workspace_metadata(
    artifact: Artifact,
    ws: WorkspaceRef,
    provider_id: str,
) -> Artifact:
    metadata = {"workspace_id": ws.id, "provider": provider_id, **dict(artifact.metadata)}
    return replace(artifact, metadata=metadata)


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
