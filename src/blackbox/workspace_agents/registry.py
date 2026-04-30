from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

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
        published = replace(
            current,
            publication=PublicationSpec(
                visibility=visibility,
                directory_enabled=directory_enabled,
                approved=current.publication.approved,
                channels=list(current.publication.channels),
                metadata=dict(current.publication.metadata),
            ),
        )
        return await self.save(published)

    async def deprecate(
        self,
        agent_id: str,
        *,
        reason: str | None = None,
    ) -> WorkspaceAgentSpec:
        current = await self.get(agent_id)
        metadata = dict(current.publication.metadata)
        metadata["deprecated"] = True
        if reason is not None:
            metadata["deprecation_reason"] = reason
        deprecated = replace(
            current,
            publication=PublicationSpec(
                visibility="unlisted",
                directory_enabled=False,
                approved=current.publication.approved,
                channels=list(current.publication.channels),
                metadata=metadata,
            ),
        )
        return await self.save(deprecated)
