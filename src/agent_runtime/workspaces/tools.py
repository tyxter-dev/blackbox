from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.tools.registry import ToolCallable, ToolDefinition
from agent_runtime.tools.results import ToolResult
from agent_runtime.workspaces.changes import ChangeType, CommandSpec, FileChange, Patch
from agent_runtime.workspaces.serialization import workspace_state_to_dict
from agent_runtime.workspaces.spec import WorkspaceRef

RegisterTool = Callable[
    [ToolCallable],
    ToolDefinition,
]


def register_workspace_tools(
    register: Callable[..., ToolDefinition],
    workspace: Any,
    ref: WorkspaceRef,
    *,
    prefix: str = "workspace",
) -> list[ToolDefinition]:
    """Register file, patch, command, snapshot, and port tools bound to a workspace ref."""
    async def read_file(path: str) -> ToolResult:
        content = await workspace.read_file(ref, path)
        return _tool_result(
            workspace,
            ref,
            content=content,
            payload={"path": path, "content": content},
        )

    async def write_file(path: str, content: str) -> ToolResult:
        change = await workspace.write_file(ref, path, content)
        return _tool_result(
            workspace,
            ref,
            content=f"Wrote {path}.",
            payload={"change": change},
        )

    async def delete_file(path: str) -> ToolResult:
        change = await workspace.delete_file(ref, path)
        return _tool_result(
            workspace,
            ref,
            content=f"Deleted {path}.",
            payload={"change": change},
        )

    async def list_files(
        path: str = ".",
        recursive: bool = False,
        limit: int = 1000,
    ) -> ToolResult:
        files = await workspace.list_files(ref, path=path, recursive=recursive, limit=limit)
        return _tool_result(
            workspace,
            ref,
            content="\n".join(files) if files else "No files found.",
            payload={"files": files},
        )

    async def apply_patch(
        summary: str,
        diff: str,
        changes: list[dict[str, Any]],
    ) -> ToolResult:
        patch = Patch(
            summary=summary,
            diff=diff,
            changes=[
                FileChange(
                    path=str(change["path"]),
                    type=cast(ChangeType, change["type"]),
                    content=cast(str | None, change.get("content")),
                    old_path=cast(str | None, change.get("old_path")),
                )
                for change in changes
            ],
        )
        artifact = await workspace.apply_patch(ref, patch)
        return _tool_result(
            workspace,
            ref,
            content=f"Created patch artifact {artifact.id}.",
            payload={"artifact": artifact},
        )

    async def run_command(
        command: str,
        cwd: str | None = None,
        timeout_seconds: float | None = None,
    ) -> ToolResult:
        result = await workspace.run_command(
            ref,
            CommandSpec(command=command, cwd=cwd, timeout=timeout_seconds),
        )
        return _tool_result(
            workspace,
            ref,
            content=result.stdout or result.stderr or f"Command exited {result.exit_code}.",
            payload={"result": result},
            metadata={"exit_code": result.exit_code, "command_id": result.command_id},
            artifacts=list(result.artifacts),
        )

    async def snapshot(name: str | None = None) -> ToolResult:
        artifact = await workspace.snapshot(ref, name=name)
        return _tool_result(
            workspace,
            ref,
            content=f"Created workspace snapshot {artifact.id}.",
            payload={"artifact": artifact},
            artifacts=[artifact],
        )

    async def expose_port(
        port: int,
        protocol: str = "http",
        name: str | None = None,
    ) -> ToolResult:
        exposed = await workspace.expose_port(ref, port, protocol=protocol, name=name)
        return _tool_result(
            workspace,
            ref,
            content=exposed.url or f"Exposed {protocol} port {port}.",
            payload={"port": exposed},
        )

    tool_specs: list[tuple[ToolCallable, str, str, dict[str, Any]]] = [
        (
            read_file,
            f"{prefix}_read_file",
            "Read a text file from the current workspace.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        (
            write_file,
            f"{prefix}_write_file",
            "Write a text file in the current workspace.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        ),
        (
            delete_file,
            f"{prefix}_delete_file",
            "Delete a file from the current workspace.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        (
            list_files,
            f"{prefix}_list_files",
            "List files in the current workspace.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "recursive": {"type": "boolean", "default": False},
                    "limit": {"type": "integer", "default": 1000},
                },
            },
        ),
        (
            apply_patch,
            f"{prefix}_apply_patch",
            "Apply a structured patch to the current workspace.",
            {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "diff": {"type": "string"},
                    "changes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "type": {
                                    "type": "string",
                                    "enum": ["create", "update", "delete"],
                                },
                                "content": {"type": "string"},
                                "old_path": {"type": "string"},
                            },
                            "required": ["path", "type"],
                        },
                    },
                },
                "required": ["summary", "diff", "changes"],
            },
        ),
        (
            run_command,
            f"{prefix}_run_command",
            "Run a command in the current workspace.",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout_seconds": {"type": "number"},
                },
                "required": ["command"],
            },
        ),
        (
            snapshot,
            f"{prefix}_snapshot",
            "Create a snapshot artifact for the current workspace.",
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        ),
        (
            expose_port,
            f"{prefix}_expose_port",
            "Expose a network port from the current workspace.",
            {
                "type": "object",
                "properties": {
                    "port": {"type": "integer"},
                    "protocol": {"type": "string", "enum": ["http", "https", "tcp"]},
                    "name": {"type": "string"},
                },
                "required": ["port"],
            },
        ),
    ]
    return [
        register(
            function,
            name=name,
            description=description,
            parameters=parameters,
            category="workspace",
            tags=["workspace"],
            metadata={
                "workspace_id": ref.id,
                "workspace_kind": ref.kind,
                "workspace_provider": getattr(workspace, "provider_id", ref.provider),
            },
        )
        for function, name, description, parameters in tool_specs
    ]


def _tool_result(
    workspace: Any,
    ref: WorkspaceRef,
    *,
    content: str,
    payload: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    artifacts: list[Artifact] | None = None,
) -> ToolResult:
    """Wrap workspace operation output with drained events, artifacts, and session metadata."""
    events = _drain_events(workspace)
    event_artifacts = _artifacts_from_events(events)
    all_artifacts = _dedupe_artifacts([*(artifacts or []), *event_artifacts])
    return ToolResult(
        content=content,
        payload=payload,
        metadata={
            "backend": "workspace",
            "workspace_id": ref.id,
            "workspace_kind": ref.kind,
            "workspace_provider": getattr(workspace, "provider_id", ref.provider),
            **_session_metadata(workspace, ref),
            **(metadata or {}),
        },
        events=events,
        artifacts=all_artifacts,
    )


def _drain_events(workspace: Any) -> list[AgentEvent]:
    drain = getattr(workspace, "drain_events", None)
    if drain is None:
        return []
    events = drain()
    return list(events) if isinstance(events, list) else []


def _artifacts_from_events(events: list[AgentEvent]) -> list[Artifact]:
    artifacts: list[Artifact] = []
    for event in events:
        if event.type != EventTypes.ARTIFACT_CREATED:
            continue
        artifact = event.data.get("artifact")
        if isinstance(artifact, Artifact):
            artifacts.append(artifact)
    return artifacts


def _dedupe_artifacts(artifacts: list[Artifact]) -> list[Artifact]:
    seen: set[str] = set()
    deduped: list[Artifact] = []
    for artifact in artifacts:
        if artifact.id in seen:
            continue
        seen.add(artifact.id)
        deduped.append(artifact)
    return deduped


def _session_metadata(workspace: Any, ref: WorkspaceRef) -> dict[str, Any]:
    session_state = getattr(workspace, "session_state", None)
    if session_state is None:
        return {}
    try:
        state = session_state(ref)
    except Exception:
        return {}
    return {"workspace_session_state": workspace_state_to_dict(state)}
