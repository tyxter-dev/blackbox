from __future__ import annotations

from pathlib import Path

import pytest

from blackbox.core.errors import WorkspaceError
from blackbox.core.events import EventTypes
from blackbox.workspaces import LocalWorkspaceProvider, WorkspaceSpec
from blackbox.workspaces.changes import CommandResult, CommandSpec, FileChange, Patch


async def test_local_provider_lists_relative_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')")
    provider = LocalWorkspaceProvider()
    ws = await provider.open(WorkspaceSpec.local(tmp_path))
    provider.drain_events()

    files = await provider.list_files(ws, path=".", recursive=True)

    assert files == ["src/app.py"]
    assert provider.drain_events()[-1].type == EventTypes.WORKSPACE_FILE_LISTED


async def test_local_snapshot_creates_archive_and_restore_rehydrates_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1")
    provider = LocalWorkspaceProvider()
    ws = await provider.open(WorkspaceSpec.local(tmp_path))

    snapshot = await provider.snapshot(ws, name="before-change")
    restored = await provider.restore(snapshot.ref)

    assert snapshot.type == "workspace_snapshot"
    assert snapshot.uri is not None
    assert snapshot.uri.startswith("file://")
    assert restored.root is not None
    assert (Path(restored.root) / "src" / "app.py").read_text() == "value = 1"
    event_types = [event.type for event in provider.drain_events()]
    assert EventTypes.WORKSPACE_SNAPSHOT_CREATED in event_types
    assert EventTypes.WORKSPACE_SNAPSHOT_RESTORED in event_types


async def test_local_patch_artifact_is_lazy_until_export(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider()
    ws = await provider.open(WorkspaceSpec.local(tmp_path))
    patch = Patch(
        summary="add app",
        diff="--- /dev/null\n+++ b/app.py\n@@\n+print('ok')\n",
        changes=[FileChange(path="app.py", type="create", content="print('ok')")],
    )

    patch_artifact = await provider.apply_patch(ws, patch)
    page = provider.list_artifacts(ws, type="patch")
    lazy_artifact = page.items[0]
    exported = await provider.export_artifact(ws, lazy_artifact.ref)

    assert lazy_artifact.id == patch_artifact.id
    assert lazy_artifact.data is None
    assert lazy_artifact.metadata["lazy"] is True
    assert lazy_artifact.metadata["diff_bytes"] == len(patch.diff.encode("utf-8"))
    assert exported.data is not None
    assert exported.data["diff"] == patch.diff


async def test_local_command_output_artifact_is_lazy_until_export(tmp_path: Path) -> None:
    async def executor(spec: CommandSpec, ws: object) -> CommandResult:
        return CommandResult(exit_code=0, stdout="0123456789", stderr="")

    provider = LocalWorkspaceProvider(executor=executor, command_output_limit=4)
    ws = await provider.open(WorkspaceSpec.local(tmp_path))

    result = await provider.run_command(
        ws,
        CommandSpec(command="echo output", metadata={"artifact": True}),
    )
    lazy_artifact = result.artifacts[0]
    exported = await provider.export_artifact(ws, lazy_artifact.ref)

    assert lazy_artifact.data is None
    assert lazy_artifact.metadata["lazy"] is True
    assert lazy_artifact.metadata["data_bytes"] == 10
    assert exported.data == {"stdout": "0123456789", "stderr": "", "exit_code": 0}


async def test_local_provider_rejects_list_path_escape(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider()
    ws = await provider.open(WorkspaceSpec.local(tmp_path))

    with pytest.raises(WorkspaceError, match="escapes workspace root"):
        await provider.list_files(ws, path="../outside", recursive=True)
