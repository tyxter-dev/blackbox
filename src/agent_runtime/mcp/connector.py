from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.errors import ApprovalError, MCPError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.policy import Policy, PolicyDecision, PolicyRequest
from agent_runtime.mcp.auth import MCPAuthProvider
from agent_runtime.mcp.cache import MCPToolCacheEntry
from agent_runtime.mcp.client import MCPClient
from agent_runtime.mcp.spec import MCPServerSpec
from agent_runtime.mcp.transports import MCPTransportClient, transport_for_spec
from agent_runtime.tools.registry import ToolDefinition, ToolRegistry

MCPToolCallable = Callable[..., Any]


@dataclass(slots=True, frozen=True)
class MCPToolDefinition:
    server: str
    name: str
    function: MCPToolCallable | None = None
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"mcp:{self.server}.{self.name}"

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "type": "mcp_tool",
            "server": self.server,
            "server_label": self.server,
            "name": self.name,
            "ref": self.ref,
            "description": self.description,
            "parameters": self.parameters,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class MCPConnector:
    """MCP dispatch connector.

    In-process registered tools remain supported for tests and embedded
    servers. Specs with a command or URL are managed through stdio/HTTP
    transports that speak MCP JSON-RPC.
    """

    servers: list[MCPServerSpec]
    policy: Policy | None = None
    auth_provider: MCPAuthProvider | None = None
    transports: dict[str, MCPTransportClient] = field(default_factory=dict)
    events: list[AgentEvent] = field(default_factory=list)
    _tools: dict[str, MCPToolDefinition] = field(default_factory=dict)
    _clients: dict[str, MCPClient] = field(default_factory=dict)
    _tool_cache: dict[str, MCPToolCacheEntry] = field(default_factory=dict)

    def register_tool(
        self,
        server: str,
        name: str,
        function: MCPToolCallable,
        *,
        description: str = "",
        parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MCPToolDefinition:
        if server not in {spec.name for spec in self.servers}:
            raise MCPError(f"Unknown MCP server: {server}")
        definition = MCPToolDefinition(
            server=server,
            name=name,
            function=function,
            description=description,
            parameters=parameters or {"type": "object"},
            metadata=metadata or {},
        )
        self._tools[definition.ref] = definition
        return definition

    async def list_tools(self, server: str | None = None) -> list[dict[str, Any]]:
        if server is not None:
            await self._ensure_discovered(server)
        else:
            for spec in self.servers:
                await self._ensure_discovered(spec.name)
        tools = [
            tool.to_descriptor()
            for tool in self._tools.values()
            if server is None or tool.server == server
        ]
        self._emit(
            EventTypes.MCP_LIST_TOOLS_COMPLETED,
            data={"server": server, "server_label": server, "tools": tools},
        )
        return tools

    async def start(self) -> None:
        for spec in self.servers:
            if self._is_managed(spec):
                await self._client_for(spec.name).start()

    async def stop(self) -> None:
        for client in self._clients.values():
            await client.stop()
        self._clients.clear()
        self._tool_cache.clear()

    async def refresh_tools(self, server: str) -> list[dict[str, Any]]:
        self._tool_cache.pop(server, None)
        for ref in [ref for ref, definition in self._tools.items() if definition.server == server and definition.function is None]:
            self._tools.pop(ref, None)
        await self._discover_tools(server)
        return await self.list_tools(server)

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        await self._ensure_discovered(server)
        definition = self._resolve(server, tool)
        args = dict(arguments or {})
        await self._policy_gate(definition, args)
        self._emit(
            EventTypes.MCP_CALL_STARTED,
            data={
                "server": definition.server,
                "server_label": definition.server,
                "tool": definition.name,
                "name": definition.name,
                "ref": definition.ref,
                "arguments": args,
            },
        )
        if definition.function is None:
            result = await self._client_for(definition.server).call_tool(definition.name, args)
        else:
            result = definition.function(**args)
            if inspect.isawaitable(result):
                result = await result
        self._emit(
            EventTypes.MCP_CALL_COMPLETED,
            data={
                "server": definition.server,
                "server_label": definition.server,
                "tool": definition.name,
                "name": definition.name,
                "ref": definition.ref,
                "result": result,
                "output": result,
            },
        )
        return result

    async def to_runtime_tools(self) -> list[dict[str, Any]]:
        return await self.list_tools()

    async def register_runtime_tools(
        self,
        registry: ToolRegistry,
        *,
        server: str | None = None,
    ) -> list[ToolDefinition]:
        definitions: list[ToolDefinition] = []
        for descriptor in await self.list_tools(server):
            ref = str(descriptor["ref"])
            tool_server = str(descriptor["server"])
            tool_name = str(descriptor["name"])

            async def call_mcp_tool(
                _server: str = tool_server,
                _tool: str = tool_name,
                **arguments: Any,
            ) -> Any:
                return await self.call_tool(_server, _tool, arguments)

            definitions.append(
                registry.register(
                    call_mcp_tool,
                    name=ref,
                    description=str(descriptor.get("description") or ref),
                    parameters=dict(descriptor.get("parameters") or {"type": "object"}),
                    metadata={
                        "mcp": True,
                        "server": tool_server,
                        "tool": tool_name,
                        "ref": ref,
                    },
                )
            )
        return definitions

    def drain_events(self) -> list[AgentEvent]:
        drained = list(self.events)
        self.events.clear()
        return drained

    def _resolve(self, server: str, tool: str) -> MCPToolDefinition:
        # Remote discovery is async. Callers should list tools first for managed
        # transports; this keeps the sync resolver simple for hot-path calls.
        if tool.startswith("mcp:"):
            ref = tool
        else:
            ref = f"mcp:{server}.{tool}"
        try:
            return self._tools[ref]
        except KeyError as exc:
            raise MCPError(f"Unknown MCP tool: {ref}") from exc

    async def _policy_gate(
        self, definition: MCPToolDefinition, arguments: dict[str, Any]
    ) -> None:
        if self.policy is None:
            return
        decision: PolicyDecision = await self.policy.check(
            PolicyRequest(
                checkpoint="before_mcp_call",
                action=definition.ref,
                arguments=arguments,
                metadata={"server": definition.server, "tool": definition.name},
            )
        )
        if decision.verdict == "allow":
            return
        if decision.verdict == "deny":
            raise MCPError(
                f"Policy denied MCP call {definition.ref}: "
                f"{decision.reason or 'no reason provided'}"
            )
        self._emit(
            EventTypes.MCP_APPROVAL_REQUIRED,
            data={
                "server": definition.server,
                "server_label": definition.server,
                "tool": definition.name,
                "name": definition.name,
                "ref": definition.ref,
                "reason": decision.reason,
            },
        )
        raise ApprovalError(
            f"Policy required approval for MCP call {definition.ref}. "
            "MCP approval resume is not wired yet."
        )

    def _emit(self, type_: str, *, data: dict[str, Any]) -> None:
        self.events.append(AgentEvent(type=type_, provider="mcp", data=data))

    async def _ensure_discovered(self, server: str) -> None:
        spec = self._server_spec(server)
        if not self._is_managed(spec):
            return
        cached = self._tool_cache.get(server)
        if cached is not None and not cached.expired():
            return
        await self._discover_tools(server)

    async def _discover_tools(self, server: str) -> None:
        spec = self._server_spec(server)
        tools = await self._client_for(server).list_tools()
        definitions: dict[str, MCPToolDefinition] = {}
        for tool in tools:
            name = str(tool.get("name", ""))
            if not name:
                continue
            definition = MCPToolDefinition(
                server=server,
                name=name,
                description=str(tool.get("description", "")),
                parameters=dict(
                    tool.get("inputSchema")
                    or tool.get("input_schema")
                    or tool.get("parameters")
                    or {"type": "object"}
                ),
                metadata={"raw": tool, "transport": spec.transport},
            )
            definitions[definition.ref] = definition
            self._tools[definition.ref] = definition
        self._tool_cache[server] = MCPToolCacheEntry(
            tools=definitions,
            ttl_seconds=spec.tool_cache_ttl_seconds,
        )

    def _client_for(self, server: str) -> MCPClient:
        existing = self._clients.get(server)
        if existing is not None:
            return existing
        spec = self._server_spec(server)
        transport = self.transports.get(server)
        if transport is None:
            transport = transport_for_spec(spec, auth_provider=self.auth_provider)
        client = MCPClient(spec=spec, transport=transport)
        self._clients[server] = client
        return client

    def _server_spec(self, server: str) -> MCPServerSpec:
        for spec in self.servers:
            if spec.name == server:
                return spec
        raise MCPError(f"Unknown MCP server: {server}")

    @staticmethod
    def _is_managed(spec: MCPServerSpec) -> bool:
        return bool(spec.command or spec.url)
