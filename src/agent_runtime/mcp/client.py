from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.mcp.spec import MCPServerSpec
from agent_runtime.mcp.transports import MCPTransportClient


@dataclass(slots=True)
class MCPClient:
    spec: MCPServerSpec
    transport: MCPTransportClient
    initialized: bool = False

    async def start(self) -> None:
        await self.transport.start()
        if self.initialized:
            return
        await self.transport.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "agent-runtime-core",
                    "version": "0.1.0",
                },
            },
        )
        self.initialized = True

    async def stop(self) -> None:
        await self.transport.stop()
        self.initialized = False

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.start()
        result = await self.transport.request("tools/list")
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [dict(tool) for tool in tools if isinstance(tool, dict)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        await self.start()
        return await self.transport.request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )

