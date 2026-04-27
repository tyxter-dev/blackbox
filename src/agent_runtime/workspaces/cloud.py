from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact, ArtifactRef
from agent_runtime.core.errors import UnsupportedFeatureError, WorkspaceError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.policy import Policy, PolicyRequest
from agent_runtime.workspaces.changes import (
    CommandResult,
    CommandSpec,
    FileChange,
    Patch,
    PatchArtifact,
)
from agent_runtime.workspaces.local import AwaitableArtifactPage
from agent_runtime.workspaces.provider import PendingWorkspaceOperation, WorkspaceApprovalRequired
from agent_runtime.workspaces.serialization import workspace_state_to_dict
from agent_runtime.workspaces.spec import (
    WorkspacePort,
    WorkspaceProviderCapabilities,
    WorkspaceRef,
    WorkspaceSessionState,
    WorkspaceSpec,
)


@dataclass(slots=True)
class CloudWorkspaceProvider:
    """Generic provider for opaque provider-hosted workspace references."""

    client: Any | None = None
    policy: Policy | None = None
    provider_id: str = "cloud-workspace"
    events: list[AgentEvent] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    _open: dict[str, WorkspaceRef] = field(default_factory=dict)

    def capabilities(self) -> WorkspaceProviderCapabilities:
        advertised = getattr(self.client, "capabilities", None)
        if callable(advertised):
            value = advertised()
            if isinstance(value, WorkspaceProviderCapabilities):
                return value
            if isinstance(value, dict):
                return WorkspaceProviderCapabilities(**value)
        return WorkspaceProviderCapabilities(
            supports_cloud_refs=True,
            supports_artifacts=True,
            supports_approvals=True,
            supports_resume=True,
        )

    async def open(self, spec: WorkspaceSpec) -> WorkspaceRef:
        if spec.kind != "cloud":
            raise UnsupportedFeatureError(
                f"CloudWorkspaceProvider supports only 'cloud' workspaces, got {spec.kind!r}."
            )
        await self._policy_gate(
            "before_workspace_open",
            workspace_id=None,
            operation="open",
            action=str(spec.metadata.get("provider_workspace_id") or spec.root or "cloud"),
            arguments={"kind": spec.kind, "root": spec.root, "metadata": dict(spec.metadata)},
        )
        if spec.session_state is not None:
            return await self.attach(spec.session_state)
        raw = await self._maybe_delegate("open", spec, required=False)
        ws = _coerce_workspace_ref(raw, provider=self.provider_id) if raw is not None else None
        if ws is None:
            ws = WorkspaceRef(
                kind="cloud",
                provider=self.provider_id,
                root=spec.root,
                provider_workspace_id=_optional_str(spec.metadata.get("provider_workspace_id")),
                provider_session_id=_optional_str(spec.metadata.get("provider_session_id")),
                metadata=dict(spec.metadata),
            )
        state = self.session_state(ws)
        ws = replace(ws, provider=self.provider_id, kind="cloud", session_state=state)
        self._open[ws.id] = ws
        self._emit(
            EventTypes.WORKSPACE_OPENED,
            ws=ws,
            data={"session_state": workspace_state_to_dict(state)},
        )
        return ws

    async def attach(self, state: WorkspaceSessionState) -> WorkspaceRef:
        if state.kind != "cloud":
            raise WorkspaceError(f"Cannot attach non-cloud workspace state: {state.kind!r}.")
        raw = await self._maybe_delegate("attach", state, required=False)
        ws = _coerce_workspace_ref(raw, provider=self.provider_id) if raw is not None else None
        if ws is None:
            ws = WorkspaceRef(
                id=state.workspace_id,
                kind="cloud",
                provider=self.provider_id,
                root=state.root,
                provider_workspace_id=state.provider_workspace_id,
                provider_session_id=state.provider_session_id,
                session_state=state,
                metadata=dict(state.metadata),
            )
        self._open[ws.id] = ws
        self._emit(
            EventTypes.WORKSPACE_OPENED,
            ws=ws,
            data={"attached": True, "session_state": workspace_state_to_dict(state)},
        )
        return ws

    async def close(self, ws: WorkspaceRef, *, delete: bool = False) -> None:
        await self._maybe_delegate("close", ws, delete=delete, required=False)
        self._open.pop(ws.id, None)
        self._emit(EventTypes.WORKSPACE_CLOSED, ws=ws, data={"delete": delete})

    async def read_file(self, ws: WorkspaceRef, path: str) -> str:
        content = await self._maybe_delegate("read_file", ws, path)
        text = str(content)
        self._emit(EventTypes.WORKSPACE_FILE_READ, ws=ws, data={"path": path, "bytes": len(text)})
        return text

    async def write_file(self, ws: WorkspaceRef, path: str, content: str) -> FileChange:
        raw = await self._maybe_delegate("write_file", ws, path, content)
        change = raw if isinstance(raw, FileChange) else FileChange(path=path, type="update", content=content)
        self._emit(EventTypes.WORKSPACE_FILE_CHANGED, ws=ws, data={"change": change})
        return change

    async def delete_file(self, ws: WorkspaceRef, path: str) -> FileChange:
        raw = await self._maybe_delegate("delete_file", ws, path)
        change = raw if isinstance(raw, FileChange) else FileChange(path=path, type="delete")
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
        raw = await self._maybe_delegate(
            "list_files",
            ws,
            path=path,
            recursive=recursive,
            limit=limit,
        )
        files = [str(item) for item in (raw or [])]
        self._emit(
            EventTypes.WORKSPACE_FILE_LISTED,
            ws=ws,
            data={"path": path, "recursive": recursive, "count": len(files)},
        )
        return files

    async def apply_patch(self, ws: WorkspaceRef, patch: Patch) -> PatchArtifact:
        raw = await self._maybe_delegate("apply_patch", ws, patch)
        artifact = raw if isinstance(raw, PatchArtifact) else PatchArtifact(
            summary=patch.summary,
            diff=patch.diff,
            changes=list(patch.changes),
            workspace=ws,
        )
        self._emit(
            EventTypes.WORKSPACE_PATCH_CREATED,
            ws=ws,
            data={"patch_id": artifact.id, "summary": artifact.summary},
        )
        as_artifact = artifact.to_artifact()
        self.artifacts.append(as_artifact)
        self._emit(EventTypes.ARTIFACT_CREATED, ws=ws, data={"artifact": as_artifact})
        return artifact

    async def run_command(self, ws: WorkspaceRef, spec: CommandSpec) -> CommandResult:
        self._emit(
            EventTypes.WORKSPACE_COMMAND_STARTED,
            ws=ws,
            data={"command": spec.display},
        )
        raw = await self._maybe_delegate("run_command", ws, spec)
        result = raw if isinstance(raw, CommandResult) else CommandResult(**dict(raw or {}))
        if result.stdout:
            self._emit(
                EventTypes.WORKSPACE_COMMAND_OUTPUT,
                ws=ws,
                data={"command_id": result.command_id, "stream": "stdout", "text": result.stdout},
            )
        if result.stderr:
            self._emit(
                EventTypes.WORKSPACE_COMMAND_OUTPUT,
                ws=ws,
                data={"command_id": result.command_id, "stream": "stderr", "text": result.stderr},
            )
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

    async def stream_command(self, ws: WorkspaceRef, spec: CommandSpec) -> AsyncIterator[AgentEvent]:
        stream = getattr(self.client, "stream_command", None)
        if callable(stream):
            async for raw in stream(ws, spec):
                event = _coerce_event(raw, ws=ws, provider=self.provider_id)
                self.events.append(event)
                yield event
            return
        await self.run_command(ws, spec)
        for event in self.drain_events():
            yield event

    async def snapshot(self, ws: WorkspaceRef, *, name: str | None = None) -> Artifact:
        raw = await self._maybe_delegate("snapshot", ws, name=name)
        artifact = _coerce_artifact(raw, fallback_name=name or f"snapshot_{ws.id}")
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
        raw = await self._maybe_delegate("restore", snapshot, spec=spec)
        ws = _coerce_workspace_ref(raw, provider=self.provider_id)
        if ws is None:
            raise WorkspaceError(f"Cloud restore returned no workspace for snapshot {snapshot.id}.")
        ws = replace(ws, kind="cloud", provider=self.provider_id, snapshot=snapshot)
        self._open[ws.id] = ws
        self._emit(
            EventTypes.WORKSPACE_SNAPSHOT_RESTORED,
            ws=ws,
            data={"snapshot_id": snapshot.id},
        )
        return ws

    async def expose_port(
        self,
        ws: WorkspaceRef,
        port: int,
        *,
        protocol: str = "http",
        name: str | None = None,
    ) -> WorkspacePort:
        raw = await self._maybe_delegate("expose_port", ws, port, protocol=protocol, name=name)
        exposed = raw if isinstance(raw, WorkspacePort) else WorkspacePort(
            workspace_id=ws.id,
            port=port,
            protocol=protocol,  # type: ignore[arg-type]
            name=name,
        )
        self._emit(EventTypes.WORKSPACE_PORT_EXPOSED, ws=ws, data={"port": exposed})
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
        return AwaitableArtifactPage(
            items=page,
            next_cursor=page[-1].id if len(items) > limit and page else None,
            has_more=len(items) > limit,
        )

    async def export_artifact(self, ws: WorkspaceRef, ref: ArtifactRef) -> Artifact:
        raw = await self._maybe_delegate("export_artifact", ws, ref, required=False)
        artifact = _coerce_artifact(raw, fallback_name=ref.id) if raw is not None else None
        if artifact is None:
            artifact = Artifact(id=ref.id, type="provider_ref", name=ref.id, uri=ref.uri)
        artifact = _with_workspace_metadata(artifact, ws, self.provider_id)
        self._emit(EventTypes.WORKSPACE_ARTIFACT_EXPORTED, ws=ws, data={"artifact": artifact})
        return artifact

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        await self._maybe_delegate("approve", approval_id, decision, required=False)

    def session_state(self, ws: WorkspaceRef) -> WorkspaceSessionState:
        client_state = getattr(self.client, "session_state", None)
        if callable(client_state):
            raw = client_state(ws)
            if isinstance(raw, WorkspaceSessionState):
                return raw
        return WorkspaceSessionState(
            provider=self.provider_id,
            workspace_id=ws.id,
            kind="cloud",
            provider_workspace_id=ws.provider_workspace_id,
            provider_session_id=ws.provider_session_id,
            root=ws.root,
            metadata=dict(ws.metadata),
        )

    def drain_events(self) -> list[AgentEvent]:
        events = list(self.events)
        self.events.clear()
        return events

    async def _maybe_delegate(
        self,
        method_name: str,
        *args: Any,
        required: bool = True,
        **kwargs: Any,
    ) -> Any:
        method = getattr(self.client, method_name, None)
        if not callable(method):
            if required:
                raise UnsupportedFeatureError(
                    f"Cloud workspace client does not support {method_name}()."
                )
            return None
        value = method(*args, **kwargs)
        if inspect.isawaitable(value):
            return await value
        return value

    async def _policy_gate(
        self,
        checkpoint: str,
        *,
        workspace_id: str | None,
        operation: str,
        action: str,
        arguments: dict[str, Any],
    ) -> None:
        if self.policy is None:
            return
        decision = await self.policy.check(
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
            id=f"workspace_approval_{workspace_id or 'cloud'}_{operation}",
            workspace_id=workspace_id or "",
            provider=self.provider_id,
            operation=operation,
            arguments=dict(arguments),
            reason=decision.reason,
            metadata={"checkpoint": checkpoint},
        )
        raise WorkspaceApprovalRequired(pending, approve=self.approve)

    def _emit(self, type_: str, *, ws: WorkspaceRef, data: dict[str, Any]) -> None:
        self.events.append(
            AgentEvent(
                type=type_,
                provider=self.provider_id,
                data={
                    "workspace_id": ws.id,
                    "workspace_kind": ws.kind,
                    "workspace_provider": self.provider_id,
                    **data,
                },
            )
        )


def _coerce_workspace_ref(raw: Any, *, provider: str) -> WorkspaceRef | None:
    if raw is None:
        return None
    if isinstance(raw, WorkspaceRef):
        return raw
    if not isinstance(raw, dict):
        return None
    workspace_kwargs: dict[str, Any] = dict(
        kind="cloud",
        provider=provider,
        root=_optional_str(raw.get("root")),
        name=_optional_str(raw.get("name")),
        provider_workspace_id=_optional_str(raw.get("provider_workspace_id")),
        provider_session_id=_optional_str(raw.get("provider_session_id")),
        metadata=dict(raw.get("metadata") or {}),
    )
    workspace_id = raw.get("id") or raw.get("workspace_id")
    if workspace_id is not None:
        workspace_kwargs["id"] = str(workspace_id)
    return WorkspaceRef(**workspace_kwargs)


def _coerce_artifact(raw: Any, *, fallback_name: str) -> Artifact:
    if isinstance(raw, Artifact):
        return raw
    data = dict(raw or {}) if isinstance(raw, dict) else {}
    artifact_kwargs: dict[str, Any] = dict(
        type=str(data.get("type", "provider_ref")),
        name=str(data.get("name") or fallback_name),
        uri=_optional_str(data.get("uri")),
        data=data.get("data"),
        metadata=dict(data.get("metadata") or {}),
    )
    if data.get("id") is not None:
        artifact_kwargs["id"] = str(data["id"])
    return Artifact(**artifact_kwargs)


def _coerce_event(raw: Any, *, ws: WorkspaceRef, provider: str) -> AgentEvent:
    if isinstance(raw, AgentEvent):
        return raw
    data = dict(raw or {}) if isinstance(raw, dict) else {"raw": raw}
    return AgentEvent(
        type=_canonical_cloud_event_type(str(data.get("type") or EventTypes.CLOUD_AGENT_LOG)),
        provider=provider,
        item_id=_optional_str(data.get("item_id")),
        data={
            "workspace_id": ws.id,
            "workspace_kind": ws.kind,
            "workspace_provider": provider,
            **dict(data.get("data") or {}),
        },
        raw=raw,
    )


def _canonical_cloud_event_type(event_type: str) -> str:
    aliases = {
        "file_read": EventTypes.WORKSPACE_FILE_READ,
        "file_changed": EventTypes.WORKSPACE_FILE_CHANGED,
        "patch": EventTypes.WORKSPACE_PATCH_CREATED,
        "patch_created": EventTypes.WORKSPACE_PATCH_CREATED,
        "command_started": EventTypes.WORKSPACE_COMMAND_STARTED,
        "command_output": EventTypes.WORKSPACE_COMMAND_OUTPUT,
        "command_completed": EventTypes.WORKSPACE_COMMAND_COMPLETED,
        "test_started": EventTypes.WORKSPACE_TEST_STARTED,
        "test_completed": EventTypes.WORKSPACE_TEST_COMPLETED,
        "snapshot_created": EventTypes.WORKSPACE_SNAPSHOT_CREATED,
        "artifact": EventTypes.ARTIFACT_CREATED,
        "approval_required": EventTypes.APPROVAL_REQUESTED,
    }
    return aliases.get(event_type, event_type)


def _with_workspace_metadata(artifact: Artifact, ws: WorkspaceRef, provider_id: str) -> Artifact:
    return replace(
        artifact,
        metadata={
            "workspace_id": ws.id,
            "provider": provider_id,
            **dict(artifact.metadata),
        },
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
