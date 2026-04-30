from __future__ import annotations

from pathlib import Path

import pytest

from blackbox.core.errors import WorkspaceError
from blackbox.core.events import EventTypes
from blackbox.workspaces import (
    CommandResult,
    CommandSpec,
    FakeSandboxClient,
    SandboxWorkspaceProvider,
    WorkspaceSpec,
)


async def test_fake_sandbox_provider_file_command_snapshot_restore_port(
    tmp_path: Path,
) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "README.md").write_text("hello")
    client = FakeSandboxClient(
        scripted_results=[CommandResult(exit_code=0, stdout="tests passed")]
    )
    provider = SandboxWorkspaceProvider(client)

    ws = await provider.open(
        WorkspaceSpec.sandbox(root="/workspace", inputs={"repo": str(tmp_path / "repo")})
    )
    provider.drain_events()

    assert await provider.read_file(ws, "repo/README.md") == "hello"
    change = await provider.write_file(ws, "repo/app.py", "print('ok')")
    files = await provider.list_files(ws, path="repo", recursive=True)
    result = await provider.run_command(ws, CommandSpec(command="pytest -q"))
    snapshot = await provider.snapshot(ws, name="after-fix")
    restored = await provider.restore(snapshot.ref)
    exposed = await provider.expose_port(ws, 8000)

    assert change.type == "create"
    assert "repo/app.py" in files
    assert result.stdout == "tests passed"
    assert snapshot.type == "workspace_snapshot"
    assert restored.kind == "sandbox"
    assert exposed.url == "http://127.0.0.1:8000"

    event_types = [event.type for event in provider.drain_events()]
    assert EventTypes.WORKSPACE_FILE_READ in event_types
    assert EventTypes.WORKSPACE_FILE_CHANGED in event_types
    assert EventTypes.WORKSPACE_COMMAND_STARTED in event_types
    assert EventTypes.WORKSPACE_COMMAND_OUTPUT in event_types
    assert EventTypes.WORKSPACE_COMMAND_COMPLETED in event_types
    assert EventTypes.WORKSPACE_SNAPSHOT_CREATED in event_types
    assert EventTypes.WORKSPACE_SNAPSHOT_RESTORED in event_types
    assert EventTypes.WORKSPACE_PORT_EXPOSED in event_types
    assert EventTypes.ARTIFACT_CREATED in event_types


async def test_sandbox_provider_rejects_path_escape() -> None:
    provider = SandboxWorkspaceProvider(FakeSandboxClient())
    ws = await provider.open(WorkspaceSpec.sandbox())

    with pytest.raises(WorkspaceError, match="escapes workspace root"):
        await provider.write_file(ws, "../outside.txt", "nope")
