from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.core.errors import WorkspaceError
from agent_runtime.core.events import EventTypes
from agent_runtime.workspaces import LocalWorkspaceProvider, WorkspaceSpec


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


async def test_local_provider_rejects_list_path_escape(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider()
    ws = await provider.open(WorkspaceSpec.local(tmp_path))

    with pytest.raises(WorkspaceError, match="escapes workspace root"):
        await provider.list_files(ws, path="../outside", recursive=True)
