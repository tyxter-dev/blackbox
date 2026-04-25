from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_runtime.mcp.spec import MCPServerSpec


@runtime_checkable
class MCPAuthProvider(Protocol):
    async def authorization_header(self, server: MCPServerSpec) -> str | None: ...

