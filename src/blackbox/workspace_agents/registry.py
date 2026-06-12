from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

from blackbox.workspace_agents.serialization import (
    workspace_agent_from_dict,
    workspace_agent_to_dict,
)
from blackbox.workspace_agents.spec import (
    PublicationSpec,
    WorkspaceAgentSpec,
    WorkspaceAgentVisibility,
)


@runtime_checkable
class WorkspaceAgentRegistry(Protocol):
    """Persistence and publication contract for workspace agent specs."""

    async def save(self, spec: WorkspaceAgentSpec) -> WorkspaceAgentSpec: ...

    async def get(self, agent_id: str, *, version: str | None = None) -> WorkspaceAgentSpec:
        """Return the requested agent version or the latest version when omitted."""
        ...

    async def list(self, *, visibility: WorkspaceAgentVisibility | None = None) -> list[WorkspaceAgentSpec]: ...

    async def publish(
        self,
        agent_id: str,
        *,
        visibility: WorkspaceAgentVisibility = "workspace",
        directory_enabled: bool = True,
    ) -> WorkspaceAgentSpec:
        """Mark an agent as publishable with the requested visibility settings."""
        ...

    async def deprecate(self, agent_id: str, *, reason: str | None = None) -> WorkspaceAgentSpec:
        """Hide an agent from the directory while preserving its stored spec."""
        ...


def published_spec(
    current: WorkspaceAgentSpec,
    *,
    visibility: WorkspaceAgentVisibility,
    directory_enabled: bool,
) -> WorkspaceAgentSpec:
    """Return ``current`` with publication settings applied."""

    return replace(
        current,
        publication=PublicationSpec(
            visibility=visibility,
            directory_enabled=directory_enabled,
            approved=current.publication.approved,
            channels=list(current.publication.channels),
            metadata=dict(current.publication.metadata),
        ),
    )


def deprecated_spec(
    current: WorkspaceAgentSpec,
    *,
    reason: str | None,
) -> WorkspaceAgentSpec:
    """Return ``current`` unlisted and flagged deprecated."""

    metadata = dict(current.publication.metadata)
    metadata["deprecated"] = True
    if reason is not None:
        metadata["deprecation_reason"] = reason
    return replace(
        current,
        publication=PublicationSpec(
            visibility="unlisted",
            directory_enabled=False,
            approved=current.publication.approved,
            channels=list(current.publication.channels),
            metadata=metadata,
        ),
    )


@dataclass(slots=True)
class InMemoryWorkspaceAgentRegistry:
    """Small registry for tests, demos, and single-process prototypes."""

    _agents: dict[str, dict[str, WorkspaceAgentSpec]] = field(default_factory=dict)
    _latest: dict[str, str] = field(default_factory=dict)

    async def save(self, spec: WorkspaceAgentSpec) -> WorkspaceAgentSpec:
        versions = self._agents.setdefault(spec.id, {})
        versions[spec.version.version] = spec
        self._latest[spec.id] = spec.version.version
        return spec

    async def get(self, agent_id: str, *, version: str | None = None) -> WorkspaceAgentSpec:
        versions = self._agents[agent_id]
        selected = version or self._latest[agent_id]
        return versions[selected]

    async def list(
        self,
        *,
        visibility: WorkspaceAgentVisibility | None = None,
    ) -> list[WorkspaceAgentSpec]:
        agents = [self._agents[agent_id][version] for agent_id, version in self._latest.items()]
        if visibility is None:
            return sorted(agents, key=lambda spec: spec.name)
        return sorted(
            [spec for spec in agents if spec.publication.visibility == visibility],
            key=lambda spec: spec.name,
        )

    async def publish(
        self,
        agent_id: str,
        *,
        visibility: WorkspaceAgentVisibility = "workspace",
        directory_enabled: bool = True,
    ) -> WorkspaceAgentSpec:
        current = await self.get(agent_id)
        return await self.save(
            published_spec(current, visibility=visibility, directory_enabled=directory_enabled)
        )

    async def deprecate(
        self,
        agent_id: str,
        *,
        reason: str | None = None,
    ) -> WorkspaceAgentSpec:
        current = await self.get(agent_id)
        return await self.save(deprecated_spec(current, reason=reason))


_SQLITE_REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_agents (
    agent_id TEXT NOT NULL,
    version TEXT NOT NULL,
    name TEXT NOT NULL,
    visibility TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (agent_id, version)
);
CREATE TABLE IF NOT EXISTS workspace_agent_latest (
    agent_id TEXT PRIMARY KEY,
    version TEXT NOT NULL
);
"""


class SQLiteWorkspaceAgentRegistry:
    """SQLite-backed :class:`WorkspaceAgentRegistry`.

    Every saved version is kept (the ``workspace_agents`` table is keyed by
    ``(agent_id, version)``); ``workspace_agent_latest`` tracks which version
    ``get``/``list`` resolve by default. Same durability model as the other
    SQLite stores: a single connection guarded by an ``asyncio.Lock`` with
    blocking work offloaded to a thread.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._connection = sqlite3.connect(str(self._path), check_same_thread=False)
        self._connection.executescript(_SQLITE_REGISTRY_SCHEMA)
        self._connection.commit()

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        self._connection.close()

    async def save(self, spec: WorkspaceAgentSpec) -> WorkspaceAgentSpec:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, spec)
        return spec

    async def get(self, agent_id: str, *, version: str | None = None) -> WorkspaceAgentSpec:
        async with self._lock:
            payload = await asyncio.to_thread(self._load_payload_sync, agent_id, version)
        if payload is None:
            raise KeyError(agent_id if version is None else f"{agent_id}@{version}")
        return workspace_agent_from_dict(json.loads(payload))

    async def list(
        self,
        *,
        visibility: WorkspaceAgentVisibility | None = None,
    ) -> list[WorkspaceAgentSpec]:
        async with self._lock:
            payloads = await asyncio.to_thread(self._list_payloads_sync, visibility)
        specs = [workspace_agent_from_dict(json.loads(payload)) for payload in payloads]
        return sorted(specs, key=lambda spec: spec.name)

    async def publish(
        self,
        agent_id: str,
        *,
        visibility: WorkspaceAgentVisibility = "workspace",
        directory_enabled: bool = True,
    ) -> WorkspaceAgentSpec:
        current = await self.get(agent_id)
        return await self.save(
            published_spec(current, visibility=visibility, directory_enabled=directory_enabled)
        )

    async def deprecate(
        self,
        agent_id: str,
        *,
        reason: str | None = None,
    ) -> WorkspaceAgentSpec:
        current = await self.get(agent_id)
        return await self.save(deprecated_spec(current, reason=reason))

    # -- sync internals --

    def _save_sync(self, spec: WorkspaceAgentSpec) -> None:
        payload = json.dumps(workspace_agent_to_dict(spec), ensure_ascii=False)
        with self._connection:
            self._connection.execute(
                "INSERT INTO workspace_agents (agent_id, version, name, visibility, payload)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(agent_id, version) DO UPDATE SET"
                " name = excluded.name, visibility = excluded.visibility,"
                " payload = excluded.payload,"
                " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                (
                    spec.id,
                    spec.version.version,
                    spec.name,
                    spec.publication.visibility,
                    payload,
                ),
            )
            self._connection.execute(
                "INSERT INTO workspace_agent_latest (agent_id, version) VALUES (?, ?)"
                " ON CONFLICT(agent_id) DO UPDATE SET version = excluded.version",
                (spec.id, spec.version.version),
            )

    def _load_payload_sync(self, agent_id: str, version: str | None) -> str | None:
        if version is None:
            row = self._connection.execute(
                "SELECT version FROM workspace_agent_latest WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            if row is None:
                return None
            version = row[0]
        row = self._connection.execute(
            "SELECT payload FROM workspace_agents WHERE agent_id = ? AND version = ?",
            (agent_id, version),
        ).fetchone()
        return None if row is None else row[0]

    # Sequence rather than list: the ``list`` method shadows the builtin in
    # this class body, so the builtin generic is unresolvable here.
    def _list_payloads_sync(self, visibility: WorkspaceAgentVisibility | None) -> Sequence[str]:
        query = (
            "SELECT a.payload FROM workspace_agents AS a"
            " JOIN workspace_agent_latest AS latest"
            " ON latest.agent_id = a.agent_id AND latest.version = a.version"
        )
        params: tuple[str, ...] = ()
        if visibility is not None:
            query += " WHERE a.visibility = ?"
            params = (visibility,)
        return [row[0] for row in self._connection.execute(query, params).fetchall()]
