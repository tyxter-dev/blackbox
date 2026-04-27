from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.errors import ApprovalError, MCPError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.policy import Policy, PolicyDecision, PolicyRequest
from agent_runtime.core.raw import RawEnvelope
from agent_runtime.mcp.auth import MCPAuthProvider
from agent_runtime.mcp.cache import MCPToolCacheEntry, mcp_tool_cache_key
from agent_runtime.mcp.client import MCPClient
from agent_runtime.mcp.spec import MCPServerSpec
from agent_runtime.mcp.transports import MCPTransportClient, transport_for_spec
from agent_runtime.tools.registry import ToolDefinition, ToolRegistry
from agent_runtime.tools.results import ToolResult

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


@dataclass(slots=True, frozen=True)
class MCPCallResult:
    server: str
    tool: str
    ref: str
    content: list[dict[str, Any]]
    structured_content: Any | None = None
    is_error: bool = False
    raw: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def rendered_content(self) -> str:
        text_parts = [
            str(block.get("text"))
            for block in self.content
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text") is not None
        ]
        if text_parts:
            return "\n".join(text_parts)
        if self.structured_content is not None:
            return json.dumps(self.structured_content, sort_keys=True)
        if self.content:
            return json.dumps(self.content, sort_keys=True)
        return "" if self.raw is None else str(self.raw)


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
        self._emit(
            EventTypes.MCP_LIST_TOOLS_STARTED,
            data={"server": server, "server_label": server},
        )
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

    async def start(self, server: str | None = None) -> None:
        specs = [self._server_spec(server)] if server is not None else self.servers
        for spec in specs:
            if self._is_managed(spec):
                self._emit_lifecycle(EventTypes.MCP_SERVER_STARTING, spec)
                await self._client_for(spec.name).start()
                self._emit_lifecycle(EventTypes.MCP_SERVER_STARTED, spec)

    async def stop(self, server: str | None = None) -> None:
        clients = (
            [(server, self._clients[server])]
            if server is not None and server in self._clients
            else list(self._clients.items())
        )
        for server_name, client in clients:
            spec = self._server_spec(server_name)
            self._emit_lifecycle(EventTypes.MCP_SERVER_STOPPING, spec)
            await client.stop()
            self._emit_lifecycle(EventTypes.MCP_SERVER_STOPPED, spec)
            self._clients.pop(server_name, None)
        if server is None:
            self._tool_cache.clear()
        else:
            self._tool_cache = {
                key: entry for key, entry in self._tool_cache.items() if entry.server != server
            }

    async def refresh_tools(self, server: str) -> list[dict[str, Any]]:
        self._invalidate_cache(server, reason="refresh")
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
        return (await self.call_tool_result(server, tool, arguments)).raw

    async def call_tool_result(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> MCPCallResult:
        await self._ensure_discovered(server)
        try:
            definition = self._resolve(server, tool)
        except MCPError:
            if not self._is_managed(self._server_spec(server)):
                raise
            await self.refresh_tools(server)
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
        try:
            if definition.function is None:
                result = await self._client_for(definition.server).call_tool(definition.name, args)
            else:
                result = definition.function(**args)
                if inspect.isawaitable(result):
                    result = await result
            call_result = self._normalize_call_result(definition, result)
            call_result = self._apply_output_limit(definition, call_result)
        except Exception as exc:
            self._emit(
                EventTypes.MCP_CALL_FAILED,
                data={
                    "server": definition.server,
                    "server_label": definition.server,
                    "tool": definition.name,
                    "name": definition.name,
                    "ref": definition.ref,
                    "error": str(exc),
                },
            )
            raise
        self._emit(
            EventTypes.MCP_CALL_COMPLETED,
            data={
                "server": definition.server,
                "server_label": definition.server,
                "tool": definition.name,
                "name": definition.name,
                "ref": definition.ref,
                "content": call_result.content,
                "structured_content": call_result.structured_content,
                "is_error": call_result.is_error,
                "metadata": call_result.metadata,
                "output": call_result.rendered_content(),
            },
            raw=RawEnvelope(
                provider="mcp",
                payload=call_result.raw,
                schema_name="mcp.tools.call.result",
                sensitivity="internal",
            ),
        )
        return call_result

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
            ) -> ToolResult:
                self.drain_events()
                result = await self.call_tool_result(_server, _tool, arguments)
                return ToolResult(
                    content=result.rendered_content(),
                    payload={
                        "mcp": {
                            "server": result.server,
                            "tool": result.tool,
                            "ref": result.ref,
                            "structured_content": result.structured_content,
                            "content": result.content,
                            "is_error": result.is_error,
                        }
                    },
                    metadata={"mcp": True, **result.metadata},
                    error="mcp_tool_error" if result.is_error else None,
                    events=self.drain_events(),
                )

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
            if self._is_managed(self._server_spec(server)):
                raise MCPError(f"Unknown MCP tool: {ref}; refresh tools and retry.") from exc
            raise MCPError(f"Unknown MCP tool: {ref}") from exc

    async def _policy_gate(
        self, definition: MCPToolDefinition, arguments: dict[str, Any]
    ) -> None:
        spec = self._server_spec(definition.server)
        if spec.require_approval == "always":
            self._emit_approval_required(definition, "MCP server requires approval.")
            raise ApprovalError(
                f"Policy required approval for MCP call {definition.ref}. "
                "MCP approval resume is handled by the high-level runtime loop."
            )
        if self.policy is None:
            return
        decision: PolicyDecision = await self.policy.check(
            PolicyRequest(
                checkpoint="before_mcp_call",
                action=definition.ref,
                arguments=arguments,
                metadata={
                    "server": definition.server,
                    "tool": definition.name,
                    "transport": spec.transport,
                    "read_only": definition.metadata.get("read_only"),
                    "destructive": definition.metadata.get("destructive"),
                },
            )
        )
        if decision.verdict == "allow":
            return
        if decision.verdict == "deny":
            self._emit(
                EventTypes.MCP_CALL_FAILED,
                data={
                    "server": definition.server,
                    "server_label": definition.server,
                    "tool": definition.name,
                    "name": definition.name,
                    "ref": definition.ref,
                    "error": "denied_by_policy",
                    "reason": decision.reason,
                },
            )
            raise MCPError(
                f"Policy denied MCP call {definition.ref}: "
                f"{decision.reason or 'no reason provided'}"
            )
        self._emit_approval_required(definition, decision.reason)
        raise ApprovalError(
            f"Policy required approval for MCP call {definition.ref}. "
            "MCP approval resume is handled by the high-level runtime loop."
        )

    def _emit(
        self,
        type_: str,
        *,
        data: dict[str, Any],
        raw: Any | None = None,
    ) -> None:
        self.events.append(AgentEvent(type=type_, provider="mcp", data=data, raw=raw))

    async def _ensure_discovered(self, server: str) -> None:
        spec = self._server_spec(server)
        if not self._is_managed(spec):
            return
        client = self._client_for(server)
        session_info = client.session_info
        cache_key = mcp_tool_cache_key(
            spec,
            session_id=session_info.session_id if session_info is not None else None,
            protocol_version=(
                session_info.protocol_version if session_info is not None else None
            ),
        )
        cached = self._tool_cache.get(cache_key)
        if cached is not None and not cached.expired():
            self._emit_cache(EventTypes.MCP_TOOLS_CACHE_HIT, spec, cached)
            return
        if cached is not None:
            self._emit_cache(EventTypes.MCP_TOOLS_CACHE_INVALIDATED, spec, cached, reason="ttl")
            self._tool_cache.pop(cache_key, None)
        self._emit(
            EventTypes.MCP_TOOLS_CACHE_MISS,
            data={"server": server, "server_label": server, "transport": spec.transport},
        )
        await self._discover_tools(server)

    async def _discover_tools(self, server: str) -> None:
        spec = self._server_spec(server)
        client = self._client_for(server)
        tools = await client.list_tools()
        session_info = client.session_info
        definitions: dict[str, MCPToolDefinition] = {}
        for tool in tools:
            name = str(tool.get("name", ""))
            if not name or not self._tool_allowed(spec, name):
                continue
            metadata = dict(tool.get("metadata") or {})
            annotations = tool.get("annotations")
            if isinstance(annotations, dict):
                metadata["annotations"] = annotations
                if "readOnlyHint" in annotations:
                    metadata["read_only"] = annotations["readOnlyHint"]
                if "destructiveHint" in annotations:
                    metadata["destructive"] = annotations["destructiveHint"]
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
                metadata={
                    **metadata,
                    "raw": tool,
                    "transport": spec.transport,
                    "server_info": (
                        session_info.server_info if session_info is not None else {}
                    ),
                    "schema_source": "mcp.tools/list",
                },
            )
            definitions[definition.ref] = definition
            self._tools[definition.ref] = definition
        cache_key = mcp_tool_cache_key(
            spec,
            session_id=session_info.session_id if session_info is not None else None,
            protocol_version=(
                session_info.protocol_version if session_info is not None else None
            ),
        )
        self._tool_cache[cache_key] = MCPToolCacheEntry(
            server=server,
            tools=definitions,
            ttl_seconds=spec.tool_cache_ttl_seconds,
            session_id=session_info.session_id if session_info is not None else None,
            protocol_version=(
                session_info.protocol_version if session_info is not None else None
            ),
            key=cache_key,
        )

    def _client_for(self, server: str) -> MCPClient:
        existing = self._clients.get(server)
        if existing is not None:
            return existing
        spec = self._server_spec(server)
        transport = self.transports.get(server)
        if transport is None:
            spec.validate_managed()
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

    def _emit_lifecycle(self, type_: str, spec: MCPServerSpec) -> None:
        client = self._clients.get(spec.name)
        session_info = client.session_info if client is not None else None
        self._emit(
            type_,
            data={
                "server": spec.name,
                "server_label": spec.name,
                "transport": spec.transport,
                "session_id": session_info.session_id if session_info is not None else None,
                "protocol_version": (
                    session_info.protocol_version if session_info is not None else None
                ),
            },
        )

    def _emit_cache(
        self,
        type_: str,
        spec: MCPServerSpec,
        entry: MCPToolCacheEntry,
        *,
        reason: str | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "server": spec.name,
            "server_label": spec.name,
            "transport": spec.transport,
            "session_id": entry.session_id,
            "protocol_version": entry.protocol_version,
            "tool_count": len(entry.tools),
        }
        if reason is not None:
            data["reason"] = reason
        self._emit(type_, data=data)

    def _invalidate_cache(self, server: str, *, reason: str) -> None:
        removed = [
            (key, entry) for key, entry in self._tool_cache.items() if entry.server == server
        ]
        spec = self._server_spec(server)
        for key, entry in removed:
            self._tool_cache.pop(key, None)
            self._emit_cache(EventTypes.MCP_TOOLS_CACHE_INVALIDATED, spec, entry, reason=reason)

    def _emit_approval_required(
        self,
        definition: MCPToolDefinition,
        reason: str | None,
    ) -> None:
        data = {
            "server": definition.server,
            "server_label": definition.server,
            "tool": definition.name,
            "name": definition.name,
            "ref": definition.ref,
            "reason": reason,
        }
        self._emit(EventTypes.MCP_APPROVAL_REQUIRED, data=data)
        self._emit(
            EventTypes.APPROVAL_REQUESTED,
            data={
                "approval_id": f"approval_{definition.ref}",
                "action": definition.ref,
                "arguments": {},
                "reason": reason,
                "metadata": {"server": definition.server, "tool": definition.name},
            },
        )

    def _tool_allowed(self, spec: MCPServerSpec, name: str) -> bool:
        if spec.allowed_tools is not None and name not in set(spec.allowed_tools):
            return False
        if spec.denied_tools is not None and name in set(spec.denied_tools):
            return False
        return True

    def _normalize_call_result(
        self,
        definition: MCPToolDefinition,
        result: Any,
    ) -> MCPCallResult:
        if isinstance(result, MCPCallResult):
            return result
        if isinstance(result, dict):
            content = result.get("content")
            content_blocks = (
                [dict(block) for block in content if isinstance(block, dict)]
                if isinstance(content, list)
                else [{"type": "json", "json": result}]
            )
            structured_content = result.get("structuredContent", result.get("structured_content"))
            is_error = bool(result.get("isError", result.get("is_error", False)))
            metadata = dict(result.get("_meta") or result.get("metadata") or {})
        else:
            content_blocks = [{"type": "text", "text": str(result)}]
            structured_content = None
            is_error = False
            metadata = {}
        return MCPCallResult(
            server=definition.server,
            tool=definition.name,
            ref=definition.ref,
            content=content_blocks,
            structured_content=structured_content,
            is_error=is_error,
            raw=result,
            metadata=metadata,
        )

    def _apply_output_limit(
        self,
        definition: MCPToolDefinition,
        result: MCPCallResult,
    ) -> MCPCallResult:
        max_output_bytes = self._server_spec(definition.server).max_output_bytes
        if max_output_bytes is None:
            return result
        rendered = result.rendered_content()
        encoded = rendered.encode("utf-8")
        if len(encoded) <= max_output_bytes:
            return result
        truncated = encoded[:max_output_bytes].decode("utf-8", errors="ignore")
        metadata = {
            **result.metadata,
            "truncated": True,
            "original_bytes": len(encoded),
            "max_output_bytes": max_output_bytes,
        }
        return MCPCallResult(
            server=result.server,
            tool=result.tool,
            ref=result.ref,
            content=[{"type": "text", "text": truncated}],
            structured_content=result.structured_content,
            is_error=result.is_error,
            raw=result.raw,
            metadata=metadata,
        )
