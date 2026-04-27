from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from agent_runtime.mcp import MCPServerSpec, MCPToolset
from agent_runtime.providers.base import AgentSpec
from agent_runtime.workspace_agents.permissions import ConnectorSpec, ToolPermission
from agent_runtime.workspace_agents.schedules import ScheduleSpec

WorkspaceAgentVisibility = Literal["private", "workspace", "public", "unlisted"]
MemoryMode = Literal["none", "session", "agent", "user"]


@dataclass(slots=True, frozen=True)
class WorkspaceAgentVersion:
    version: str = "0.1.0"
    changelog: str | None = None
    previous_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WorkspaceAgentMetadata:
    owner: str | None = None
    description: str | None = None
    labels: list[str] = field(default_factory=list)
    created_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SkillBundleRef:
    name: str
    source: str | None = None
    version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class MemorySpec:
    mode: MemoryMode = "none"
    retention_days: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class PublicationSpec:
    visibility: WorkspaceAgentVisibility = "private"
    directory_enabled: bool = False
    approved: bool = False
    channels: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class WorkspaceAgentSpec:
    """Portable package definition for a governed workspace agent."""

    name: str
    instructions: str = ""
    id: str = field(default_factory=lambda: f"wagent_{uuid4().hex}")
    model_provider: str | None = None
    model: str | None = None
    agent_provider: str | None = None
    agent_id: str | None = None
    tools: list[str] = field(default_factory=list)
    hosted_tools: list[Any] = field(default_factory=list)
    mcp_servers: list[MCPServerSpec] = field(default_factory=list)
    mcp_toolsets: list[MCPToolset] = field(default_factory=list)
    connectors: list[ConnectorSpec] = field(default_factory=list)
    permissions: list[ToolPermission] = field(default_factory=list)
    schedules: list[ScheduleSpec] = field(default_factory=list)
    skills: list[SkillBundleRef] = field(default_factory=list)
    memory: MemorySpec = field(default_factory=MemorySpec)
    publication: PublicationSpec = field(default_factory=PublicationSpec)
    version: WorkspaceAgentVersion = field(default_factory=WorkspaceAgentVersion)
    metadata: WorkspaceAgentMetadata = field(default_factory=WorkspaceAgentMetadata)
    extra: dict[str, Any] = field(default_factory=dict)

    def resolved_mcp_toolsets(self) -> list[MCPToolset]:
        """Return MCP routing specs for direct runtime execution."""

        return [*(MCPToolset(server=server) for server in self.mcp_servers), *self.mcp_toolsets]

    def to_agent_spec(self) -> AgentSpec:
        mcp_servers = [
            *self.mcp_servers,
            *(toolset.server for toolset in self.mcp_toolsets),
        ]
        return AgentSpec(
            name=self.name,
            instructions=self.instructions,
            model=self.model,
            tools=list(self.tools),
            mcp_servers=mcp_servers,
            permissions={
                "connectors": [connector.name for connector in self.connectors],
                "tools": [permission.ref for permission in self.permissions],
                "publication": self.publication.visibility,
                "mcp_servers": [server.name for server in mcp_servers],
            },
            metadata={
                "workspace_agent_id": self.id,
                "version": self.version.version,
                "owner": self.metadata.owner,
                "skills": [skill.name for skill in self.skills],
                **self.extra,
            },
        )

    def permission_for(self, ref: str) -> ToolPermission | None:
        for permission in self.permissions:
            if permission.ref == ref:
                return permission
        return None
