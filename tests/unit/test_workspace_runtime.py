from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from agent_runtime.core.errors import ApprovalError, WorkspaceError
from agent_runtime.core.events import EventTypes
from agent_runtime.core.policy import PolicyDecision, PolicyRequest
from agent_runtime.workspaces import (
    CommandResult,
    CommandSpec,
    FileChange,
    Patch,
    WorkspaceRef,
    WorkspaceRuntime,
    WorkspaceSpec,
)


class _ScriptedPolicy:
    def __init__(self, decisions: dict[str, PolicyDecision]) -> None:
        self.seen: list[PolicyRequest] = []
        self._decisions = decisions

    async def check(self, request: PolicyRequest) -> PolicyDecision:
        self.seen.append(request)
        return self._decisions.get(request.checkpoint, PolicyDecision.allow())


def _fake_executor(
    *, exit_code: int = 0, stdout: str = "", stderr: str = "", timed_out: bool = False
) -> Callable[[CommandSpec, WorkspaceRef], Awaitable[CommandResult]]:
    async def executor(spec: CommandSpec, ws: WorkspaceRef) -> CommandResult:
        return CommandResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=0.001,
            timed_out=timed_out,
        )

    return executor


async def test_open_local_workspace_returns_ref(tmp_path: Path) -> None:
    runtime = WorkspaceRuntime()
    ref = await runtime.open(WorkspaceSpec.local(tmp_path))
    assert ref.kind == "local"
    assert ref.root == str(tmp_path)
    assert ref.id.startswith("ws_")


async def test_open_rejects_non_local_workspace_kind() -> None:
    runtime = WorkspaceRuntime()
    with pytest.raises(WorkspaceError):
        await runtime.open(WorkspaceSpec(kind="cloud"))


async def test_open_rejects_missing_directory(tmp_path: Path) -> None:
    runtime = WorkspaceRuntime()
    with pytest.raises(WorkspaceError):
        await runtime.open(WorkspaceSpec.local(tmp_path / "does-not-exist"))


async def test_read_file_emits_workspace_file_read(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("world")
    runtime = WorkspaceRuntime()
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    content = await runtime.read_file(ws, "hello.txt")

    assert content == "world"
    types = [e.type for e in runtime.events]
    assert EventTypes.WORKSPACE_FILE_READ in types


async def test_write_file_emits_change_and_returns_file_change(tmp_path: Path) -> None:
    runtime = WorkspaceRuntime()
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    change = await runtime.write_file(ws, "notes/log.txt", "hello")

    assert (tmp_path / "notes" / "log.txt").read_text() == "hello"
    assert change.type == "create"
    assert change.path == "notes/log.txt"
    file_changed = [
        e for e in runtime.events if e.type == EventTypes.WORKSPACE_FILE_CHANGED
    ]
    assert file_changed and file_changed[0].data["change"] is change


async def test_write_file_denied_by_policy_raises_workspace_error(tmp_path: Path) -> None:
    policy = _ScriptedPolicy(
        {"before_workspace_write": PolicyDecision.deny("read-only sandbox")}
    )
    runtime = WorkspaceRuntime(policy=policy)
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    with pytest.raises(WorkspaceError, match="read-only sandbox"):
        await runtime.write_file(ws, "x.txt", "data")
    assert not (tmp_path / "x.txt").exists()


async def test_write_file_require_approval_raises_approval_error(tmp_path: Path) -> None:
    policy = _ScriptedPolicy(
        {"before_workspace_write": PolicyDecision.require_approval("needs review")}
    )
    runtime = WorkspaceRuntime(policy=policy)
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    with pytest.raises(ApprovalError):
        await runtime.write_file(ws, "x.txt", "data")


async def test_path_escape_is_rejected(tmp_path: Path) -> None:
    runtime = WorkspaceRuntime()
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    with pytest.raises(WorkspaceError, match="escapes workspace root"):
        await runtime.write_file(ws, "../outside.txt", "leak")


async def test_apply_patch_emits_per_change_events_and_creates_patch_artifact(
    tmp_path: Path,
) -> None:
    (tmp_path / "old.txt").write_text("stale")
    runtime = WorkspaceRuntime()
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    patch = Patch(
        summary="add new and remove old",
        diff="--- a\n+++ b\n",
        changes=[
            FileChange(path="new.txt", type="create", content="hi"),
            FileChange(path="old.txt", type="delete"),
        ],
    )

    artifact = await runtime.apply_patch(ws, patch)

    assert (tmp_path / "new.txt").read_text() == "hi"
    assert not (tmp_path / "old.txt").exists()
    assert artifact.summary == "add new and remove old"
    assert len(artifact.changes) == 2

    types = [e.type for e in runtime.events]
    assert types.count(EventTypes.WORKSPACE_FILE_CHANGED) == 2
    assert EventTypes.WORKSPACE_PATCH_CREATED in types
    assert EventTypes.ARTIFACT_CREATED in types

    page = runtime.list_artifacts(type="patch")
    assert len(page.items) == 1
    assert page.items[0].id == artifact.id


async def test_run_command_emits_started_completed_via_executor(tmp_path: Path) -> None:
    runtime = WorkspaceRuntime(executor=_fake_executor(stdout="ok"))
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    result = await runtime.run_command(ws, CommandSpec(command="echo ok"))

    assert result.succeeded
    assert result.stdout == "ok"
    types = [e.type for e in runtime.events]
    assert EventTypes.WORKSPACE_COMMAND_STARTED in types
    assert EventTypes.WORKSPACE_COMMAND_COMPLETED in types


async def test_run_command_failure_propagates_exit_code(tmp_path: Path) -> None:
    runtime = WorkspaceRuntime(executor=_fake_executor(exit_code=1, stderr="boom"))
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    result = await runtime.run_command(ws, CommandSpec(command="false"))

    assert not result.succeeded
    assert result.exit_code == 1
    completed = next(
        e for e in runtime.events if e.type == EventTypes.WORKSPACE_COMMAND_COMPLETED
    )
    assert completed.data["exit_code"] == 1


async def test_run_command_denied_by_policy_blocks_executor(tmp_path: Path) -> None:
    policy = _ScriptedPolicy({"before_command": PolicyDecision.deny("dangerous")})
    runtime = WorkspaceRuntime(
        policy=policy, executor=_fake_executor()
    )
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    with pytest.raises(WorkspaceError, match="dangerous"):
        await runtime.run_command(ws, CommandSpec(command="rm -rf /"))

    assert not any(
        e.type == EventTypes.WORKSPACE_COMMAND_STARTED for e in runtime.events
    )


async def test_snapshot_creates_artifact_and_emits_event(tmp_path: Path) -> None:
    runtime = WorkspaceRuntime()
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    artifact = await runtime.snapshot(ws, name="pre-deploy")

    assert artifact.type == "workspace_snapshot"
    assert artifact.name == "pre-deploy"
    types = [e.type for e in runtime.events]
    assert EventTypes.ARTIFACT_CREATED in types

    page = runtime.list_artifacts(type="workspace_snapshot")
    assert len(page.items) == 1


async def test_artifact_export_policy_gates_snapshot(tmp_path: Path) -> None:
    policy = _ScriptedPolicy(
        {"before_artifact_export": PolicyDecision.deny("no exports")}
    )
    runtime = WorkspaceRuntime(policy=policy)
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))

    with pytest.raises(WorkspaceError, match="no exports"):
        await runtime.snapshot(ws)
    assert runtime.list_artifacts().items == []


async def test_drain_events_returns_and_clears_buffer(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    runtime = WorkspaceRuntime()
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))
    await runtime.read_file(ws, "a.txt")
    drained = runtime.drain_events()
    assert any(e.type == EventTypes.WORKSPACE_FILE_READ for e in drained)
    assert runtime.events == []


async def test_patch_artifact_to_artifact_round_trip(tmp_path: Path) -> None:
    runtime = WorkspaceRuntime()
    ws = await runtime.open(WorkspaceSpec.local(tmp_path))
    patch = Patch(
        summary="single",
        diff="diff",
        changes=[FileChange(path="a.txt", type="create", content="x")],
    )
    artifact = await runtime.apply_patch(ws, patch)
    generic = artifact.to_artifact()
    assert generic.type == "patch"
    assert generic.id == artifact.id
    assert generic.data["diff"] == "diff"
