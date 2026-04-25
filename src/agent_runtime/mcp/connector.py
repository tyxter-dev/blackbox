from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.mcp.spec import MCPServerSpec


@dataclass(slots=True)
class MCPConnector:
    """Placeholder for local or remote MCP connector implementations."""

    servers: list[MCPServerSpec]

    async def list_tools(self) -> list[dict[str, object]]:
        raise NotImplementedError

    async def call_tool(self, server: str, tool: str, arguments: dict[str, object]) -> object:
        raise NotImplementedError
