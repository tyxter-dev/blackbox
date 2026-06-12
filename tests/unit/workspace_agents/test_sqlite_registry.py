"""SQLite-backed workspace agent registry: persistence, versions, publication."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from blackbox.workspace_agents import (
    SQLiteWorkspaceAgentRegistry,
    WorkspaceAgentRegistry,
    WorkspaceAgentSpec,
    WorkspaceAgentVersion,
)


def make_registry(tmp_path: Path) -> SQLiteWorkspaceAgentRegistry:
    return SQLiteWorkspaceAgentRegistry(tmp_path / "agents.db")


def test_satisfies_registry_protocol(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    assert isinstance(registry, WorkspaceAgentRegistry)
    registry.close()


async def test_save_get_roundtrip(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    spec = WorkspaceAgentSpec(name="reporter", instructions="Report.", model_provider="openai")
    await registry.save(spec)

    loaded = await registry.get(spec.id)
    assert loaded.name == "reporter"
    assert loaded.instructions == "Report."
    assert loaded.id == spec.id
    registry.close()


async def test_get_unknown_raises_key_error(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    with pytest.raises(KeyError):
        await registry.get("wagent_missing")
    registry.close()


async def test_versions_are_kept_and_latest_wins(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    v1 = WorkspaceAgentSpec(name="agent", version=WorkspaceAgentVersion(version="1.0.0"))
    await registry.save(v1)
    v2 = replace(v1, instructions="updated", version=WorkspaceAgentVersion(version="2.0.0"))
    await registry.save(v2)

    latest = await registry.get(v1.id)
    assert latest.version.version == "2.0.0"
    assert latest.instructions == "updated"
    pinned = await registry.get(v1.id, version="1.0.0")
    assert pinned.version.version == "1.0.0"
    with pytest.raises(KeyError):
        await registry.get(v1.id, version="9.9.9")
    registry.close()


async def test_list_filters_visibility_and_sorts(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    b = WorkspaceAgentSpec(name="bravo")
    a = WorkspaceAgentSpec(name="alpha")
    await registry.save(b)
    await registry.save(a)

    names = [spec.name for spec in await registry.list()]
    assert names == ["alpha", "bravo"]

    await registry.publish(a.id, visibility="workspace")
    published = await registry.list(visibility="workspace")
    assert [spec.name for spec in published] == ["alpha"]
    registry.close()


async def test_publish_and_deprecate(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    spec = WorkspaceAgentSpec(name="agent")
    await registry.save(spec)

    published = await registry.publish(spec.id, visibility="public")
    assert published.publication.visibility == "public"
    assert published.publication.directory_enabled is True

    deprecated = await registry.deprecate(spec.id, reason="superseded")
    assert deprecated.publication.visibility == "unlisted"
    assert deprecated.publication.metadata["deprecated"] is True
    assert deprecated.publication.metadata["deprecation_reason"] == "superseded"
    registry.close()


async def test_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "agents.db"
    registry = SQLiteWorkspaceAgentRegistry(path)
    spec = WorkspaceAgentSpec(name="durable")
    await registry.save(spec)
    registry.close()

    reopened = SQLiteWorkspaceAgentRegistry(path)
    loaded = await reopened.get(spec.id)
    assert loaded.name == "durable"
    reopened.close()
