from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.core.errors import MCPError
from agent_runtime.mcp.spec import MCPServerSpec
from agent_runtime.mcp.transports import MCPTransportClient


@dataclass(slots=True)
class MCPSessionInfo:
    server: str
    protocol_version: str
    server_capabilities: dict[str, Any]
    server_info: dict[str, Any]
    instructions: str | None = None
    session_id: str | None = None


@dataclass(slots=True)
class MCPClient:
    spec: MCPServerSpec
    transport: MCPTransportClient
    initialized: bool = False
    session_info: MCPSessionInfo | None = None

    async def start(self) -> MCPSessionInfo:
        await self.transport.start()
        if self.initialized and self.session_info is not None:
            return self.session_info
        initialize_params = {
            "protocolVersion": self.spec.protocol_version,
            "capabilities": self._client_capabilities(),
            "clientInfo": {
                "name": "agent-runtime-core",
                "version": "0.1.0",
            },
        }
        result = await self._request(
            "initialize",
            initialize_params,
            timeout_seconds=self.spec.startup_timeout_seconds,
        )
        if not isinstance(result, dict):
            await self.transport.stop()
            raise MCPError("MCP initialize returned a non-object result.")
        negotiated = str(result.get("protocolVersion") or self.spec.protocol_version)
        if negotiated not in (
            self.spec.protocol_version,
            *self.spec.protocol_version_fallbacks,
        ):
            await self.transport.stop()
            raise MCPError(f"MCP server negotiated unsupported protocol version: {negotiated}")
        set_protocol_version = getattr(self.transport, "set_protocol_version", None)
        if callable(set_protocol_version):
            set_protocol_version(negotiated)
        server_capabilities = dict(result.get("capabilities") or {})
        server_info = dict(result.get("serverInfo") or result.get("server_info") or {})
        self.session_info = MCPSessionInfo(
            server=self.spec.name,
            protocol_version=negotiated,
            server_capabilities=server_capabilities,
            server_info=server_info,
            instructions=result.get("instructions")
            if isinstance(result.get("instructions"), str)
            else None,
            session_id=getattr(self.transport, "session_id", None),
        )
        notify = getattr(self.transport, "notify", None)
        if callable(notify):
            await notify("notifications/initialized")
        self.initialized = True
        return self.session_info

    async def stop(self) -> None:
        await self.transport.stop()
        self.initialized = False
        self.session_info = None

    async def list_tools(self) -> list[dict[str, Any]]:
        await self.start()
        if self.session_info is not None:
            capabilities = self.session_info.server_capabilities
            if capabilities and "tools" not in capabilities:
                raise MCPError(f"MCP server '{self.spec.name}' did not advertise tools.")
        result = await self._request("tools/list")
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [self._normalize_tool(tool) for tool in tools if isinstance(tool, dict)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        await self.start()
        return await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )

    def _client_capabilities(self) -> dict[str, Any]:
        return {"roots": {"listChanged": False}, "sampling": {}}

    async def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        try:
            return await self.transport.request(
                method,
                params,
                timeout_seconds=timeout_seconds,
            )
        except TypeError as exc:
            if "timeout_seconds" not in str(exc):
                raise
            return await self.transport.request(method, params)

    @staticmethod
    def _normalize_tool(tool: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(tool)
        if "inputSchema" not in normalized and "input_schema" in normalized:
            normalized["inputSchema"] = normalized["input_schema"]
        normalized.setdefault("inputSchema", {"type": "object"})
        metadata: dict[str, Any] = dict(normalized.get("metadata") or {})
        for key in ("annotations", "_meta", "icons"):
            if key in normalized:
                metadata[key] = normalized[key]
        if metadata:
            normalized["metadata"] = metadata
        return normalized
