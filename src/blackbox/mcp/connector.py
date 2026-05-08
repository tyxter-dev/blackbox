from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from blackbox.core.errors import ApprovalError, MCPAuthenticationError, MCPError
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.policy import Policy, PolicyDecision, PolicyRequest
from blackbox.core.raw import RawEnvelope
from blackbox.mcp.auth import MCPAuthProvider
from blackbox.mcp.cache import MCPToolCacheEntry, mcp_tool_cache_key
from blackbox.mcp.client import MCPClient
from blackbox.mcp.spec import MCPServerSpec
from blackbox.mcp.transports import MCPTransportClient, transport_for_spec
from blackbox.mcp.trust import (
    DefaultMCPTrustEvaluator,
    MCPTrustDecision,
    MCPTrustEvaluator,
    default_taint_for_tool,
    effective_server_trust_policy,
    sanitize_tool_description,
    trust_fingerprint,
)
from blackbox.tools.registry import ToolDefinition, ToolRegistry
from blackbox.tools.results import ToolResult

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
    trust_evaluator: MCPTrustEvaluator = field(default_factory=DefaultMCPTrustEvaluator)
    transports: dict[str, MCPTransportClient] = field(default_factory=dict)
    events: list[AgentEvent] = field(default_factory=list)
    _tools: dict[str, MCPToolDefinition] = field(default_factory=dict)
    _clients: dict[str, MCPClient] = field(default_factory=dict)
    _tool_cache: dict[str, MCPToolCacheEntry] = field(default_factory=dict)
    _server_decisions: dict[str, MCPTrustDecision] = field(default_factory=dict)
    _risk_profiles: dict[str, Any] = field(default_factory=dict)

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
        """Register an in-process MCP tool for an existing server spec."""
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
        # Stopping a transport should not invalidate discovery results. Tool
        # schemas are keyed by server identity/protocol/auth shape and expire
        # through TTL or explicit refresh, which lets callers share caches
        # across short-lived connector instances.

    async def refresh_tools(self, server: str) -> list[dict[str, Any]]:
        self._invalidate_cache(server, reason="refresh")
        self._clear_discovered_tools(server)
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
        """Invoke an MCP tool after discovery, policy checks, and normalization.

        Managed servers are rediscovered once if the requested tool is unknown.
        """
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
            call_result = self._apply_trust_metadata(definition, call_result)
        except Exception as exc:
            spec = self._server_spec(definition.server)
            self._emit(
                EventTypes.MCP_CALL_FAILED,
                data={
                    "server": definition.server,
                    "server_label": definition.server,
                    "tool": definition.name,
                    "name": definition.name,
                    "ref": definition.ref,
                    "error": _safe_error_message(exc, spec),
                    "error_type": type(exc).__name__,
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
        """Expose discovered MCP tools through the runtime tool registry."""
        definitions: list[ToolDefinition] = []
        for descriptor in await self.list_tools(server):
            ref = str(descriptor["ref"])
            tool_server = str(descriptor["server"])
            tool_name = str(descriptor["name"])
            descriptor_metadata = descriptor.get("metadata")
            descriptor_metadata = (
                dict(descriptor_metadata) if isinstance(descriptor_metadata, dict) else {}
            )
            trust_metadata = descriptor_metadata.get("trust")
            trust_metadata = dict(trust_metadata) if isinstance(trust_metadata, dict) else {}

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
                        "trust": trust_metadata,
                    },
                )
            )
        return definitions

    def drain_events(self) -> list[AgentEvent]:
        drained = list(self.events)
        self.events.clear()
        return drained

    def trust_summary(self) -> dict[str, Any]:
        servers: dict[str, Any] = {}
        for spec in self.servers:
            decision = self._server_decisions.get(spec.name)
            exposed = [
                tool for tool in self._tools.values() if tool.server == spec.name
            ]
            servers[spec.name] = {
                "trust_level": (
                    decision.trust_level.value if decision is not None else "unknown"
                ),
                "route_mode": (
                    decision.route_mode.value if decision is not None else "unknown"
                ),
                "tools_exposed": len(exposed),
                "fingerprint": trust_fingerprint(spec),
            }
        return {"servers": servers}

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
        """Enforce server approval settings and policy before a tool call."""
        spec = self._server_spec(definition.server)
        if spec.require_approval == "always":
            self._emit_approval_required(definition, "MCP server requires approval.")
            raise ApprovalError(
                f"Policy required approval for MCP call {definition.ref}. "
                "MCP approval resume is handled by the high-level runtime loop."
            )
        trust = definition.metadata.get("trust")
        if isinstance(trust, dict) and trust.get("approval_required") is True:
            self._emit(EventTypes.MCP_CALL_APPROVAL_REQUIRED, data={
                "server": definition.server,
                "server_label": definition.server,
                "tool": definition.name,
                "name": definition.name,
                "ref": definition.ref,
                "trust_level": trust.get("trust_level"),
                "route_mode": trust.get("route_mode"),
                "fingerprint": trust.get("fingerprint"),
                "reason": "MCP trust policy requires approval.",
            })
            self._emit_approval_required(definition, "MCP trust policy requires approval.")
            raise ApprovalError(
                f"MCP trust policy required approval for MCP call {definition.ref}. "
                "Server require_approval='never' only disables provider/server approval; "
                "configure the MCP trust policy approval_mode='never' for trusted write tools."
            )
        if self.policy is None:
            return
        policy_metadata = self._policy_metadata(definition)
        decision: PolicyDecision = await self.policy.check(
            PolicyRequest(
                checkpoint="before_mcp_call",
                action=definition.ref,
                arguments=arguments,
                metadata=policy_metadata,
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
            auth_cache_identity=self._auth_cache_identity(spec),
        )
        cached = self._tool_cache.get(cache_key)
        if cached is not None and not cached.expired():
            self._load_cached_tools(cached)
            self._emit_cache(EventTypes.MCP_TOOLS_CACHE_HIT, spec, cached)
            return
        if cached is not None:
            self._emit_cache(EventTypes.MCP_TOOLS_CACHE_INVALIDATED, spec, cached, reason="ttl")
            self._tool_cache.pop(cache_key, None)
            self._clear_discovered_tools(server)
        self._emit(
            EventTypes.MCP_TOOLS_CACHE_MISS,
            data={"server": server, "server_label": server, "transport": spec.transport},
        )
        await self._discover_tools(server, extra_cache_keys=[cache_key])

    async def _discover_tools(
        self,
        server: str,
        *,
        extra_cache_keys: Iterable[str] = (),
    ) -> None:
        spec = self._server_spec(server)
        client = self._client_for(server)
        server_policy = effective_server_trust_policy(spec)
        tools = await client.list_tools()
        session_info = client.session_info
        self._emit(
            EventTypes.MCP_TOOLS_DISCOVERED,
            data={
                "server": server,
                "server_label": server,
                "transport": spec.transport,
                "tool_count": len(tools),
                "fingerprint": trust_fingerprint(spec),
            },
        )
        definitions: dict[str, MCPToolDefinition] = {}
        filtered: list[dict[str, Any]] = []
        quarantined: list[dict[str, Any]] = []
        for tool in tools:
            name = str(tool.get("name", ""))
            if not name:
                continue
            metadata = dict(tool.get("metadata") or {})
            annotations = tool.get("annotations")
            if isinstance(annotations, dict):
                metadata["annotations"] = annotations
                if "readOnlyHint" in annotations:
                    metadata["read_only"] = annotations["readOnlyHint"]
                if "destructiveHint" in annotations:
                    metadata["destructive"] = annotations["destructiveHint"]
            tool_for_decision = {**tool, "name": name, "metadata": metadata}
            tool_policy = self._tool_policy_for(spec, name)
            tool_decision = self.trust_evaluator.evaluate_tool(
                server_policy,
                tool_policy,
                tool_for_decision,
            )
            if not tool_decision.allowed:
                item = {
                    "server": server,
                    "server_label": server,
                    "tool": name,
                    "name": name,
                    "ref": f"mcp:{server}.{name}",
                    "reasons": list(tool_decision.reasons),
                    "trust_level": tool_decision.trust_level.value,
                    "fingerprint": trust_fingerprint(spec),
                }
                filtered.append(item)
                if tool_decision.metadata.get("quarantined") is True:
                    quarantined.append(item)
                    self._emit(EventTypes.MCP_TOOL_QUARANTINED, data=item)
                continue
            definition = MCPToolDefinition(
                server=server,
                name=name,
                description=sanitize_tool_description(str(tool.get("description", ""))),
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
                    "trust": {
                        "trust_level": tool_decision.trust_level.value,
                        "route_mode": tool_decision.route_mode.value,
                        "approval_required": tool_decision.approval_required,
                        "reasons": list(tool_decision.reasons),
                        "risks": list(tool_decision.metadata.get("risks") or []),
                        "required_scopes": list(
                            tool_decision.metadata.get("required_scopes") or []
                        ),
                        "max_output_bytes": (
                            tool_policy.max_output_bytes
                            if tool_policy is not None
                            and tool_policy.max_output_bytes is not None
                            else server_policy.max_output_bytes
                        ),
                        "fingerprint": trust_fingerprint(spec),
                    },
                },
            )
            definitions[definition.ref] = definition
            self._tools[definition.ref] = definition
        self._emit(
            EventTypes.MCP_TOOLS_FILTERED,
            data={
                "server": server,
                "server_label": server,
                "transport": spec.transport,
                "discovered_count": len(tools),
                "exposed_count": len(definitions),
                "filtered_count": len(filtered),
                "quarantined_count": len(quarantined),
                "filtered_tools": filtered,
                "fingerprint": trust_fingerprint(spec),
            },
        )
        cache_key = mcp_tool_cache_key(
            spec,
            session_id=session_info.session_id if session_info is not None else None,
            protocol_version=(
                session_info.protocol_version if session_info is not None else None
            ),
            auth_cache_identity=self._auth_cache_identity(spec),
        )
        for key in {cache_key, *extra_cache_keys}:
            self._tool_cache[key] = MCPToolCacheEntry(
                server=server,
                tools=definitions,
                ttl_seconds=spec.tool_cache_ttl_seconds,
                session_id=session_info.session_id if session_info is not None else None,
                protocol_version=(
                    session_info.protocol_version if session_info is not None else None
                ),
                key=key,
            )

    def _load_cached_tools(self, entry: MCPToolCacheEntry) -> None:
        for ref, definition in entry.tools.items():
            if isinstance(ref, str) and isinstance(definition, MCPToolDefinition):
                self._tools[ref] = definition

    def _client_for(self, server: str) -> MCPClient:
        existing = self._clients.get(server)
        if existing is not None:
            return existing
        spec = self._server_spec(server)
        transport = self.transports.get(server)
        if transport is None:
            self._validate_and_evaluate_server(spec)
            spec.validate_managed()
            transport = transport_for_spec(spec, auth_provider=self.auth_provider)
        else:
            self._validate_and_evaluate_server(spec)

        def handle_notification(
            method: str,
            params: dict[str, Any] | None,
            server_name: str = server,
        ) -> None:
            self._handle_notification(server_name, method, params)

        client = MCPClient(
            spec=spec,
            transport=transport,
            notification_handler=handle_notification,
        )
        self._clients[server] = client
        return client

    def _handle_notification(
        self,
        server: str,
        method: str,
        params: dict[str, Any] | None,
    ) -> None:
        if method not in {
            "notifications/tools/list_changed",
            "notifications/tools/listChanged",
            "tools/listChanged",
        }:
            return
        self._invalidate_cache(server, reason="list_changed")
        self._clear_discovered_tools(server)

    def _validate_and_evaluate_server(self, spec: MCPServerSpec) -> None:
        if spec.name in self._server_decisions:
            return
        try:
            risk_profile = spec.validate_security()
        except ValueError as exc:
            self._emit(
                EventTypes.MCP_SERVER_BLOCKED,
                data={
                    "server": spec.name,
                    "server_label": spec.name,
                    "transport": spec.transport,
                    "reason": str(exc),
                    "fingerprint": trust_fingerprint(spec),
                },
            )
            raise MCPError(f"MCP server '{spec.name}' failed security validation: {exc}") from exc
        self._risk_profiles[spec.name] = risk_profile
        self._emit(
            EventTypes.MCP_SERVER_VALIDATED,
            data={
                "server": spec.name,
                "server_label": spec.name,
                "transport": spec.transport,
                "risks": sorted(risk.value for risk in risk_profile.capability_risks),
                "fingerprint": risk_profile.trust_fingerprint,
            },
        )
        policy = effective_server_trust_policy(spec)
        decision = self.trust_evaluator.evaluate_server(spec, policy)
        self._server_decisions[spec.name] = decision
        self._emit(
            EventTypes.MCP_TRUST_EVALUATED,
            data={
                "server": spec.name,
                "server_label": spec.name,
                "transport": spec.transport,
                "trust_level": decision.trust_level.value,
                "route_mode": decision.route_mode.value,
                "approval_required": decision.approval_required,
                "allowed": decision.allowed,
                "reasons": list(decision.reasons),
                "fingerprint": decision.metadata.get("fingerprint"),
            },
        )
        if not decision.allowed:
            self._emit(
                EventTypes.MCP_SERVER_BLOCKED,
                data={
                    "server": spec.name,
                    "server_label": spec.name,
                    "transport": spec.transport,
                    "trust_level": decision.trust_level.value,
                    "route_mode": decision.route_mode.value,
                    "reasons": list(decision.reasons),
                    "fingerprint": decision.metadata.get("fingerprint"),
                },
            )
            raise MCPError(f"MCP server '{spec.name}' is blocked by MCP trust policy.")

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
            "fingerprint": trust_fingerprint(spec),
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

    def _clear_discovered_tools(self, server: str) -> None:
        for ref in [
            ref
            for ref, definition in self._tools.items()
            if definition.server == server and definition.function is None
        ]:
            self._tools.pop(ref, None)

    def _auth_cache_identity(self, spec: MCPServerSpec) -> str | None:
        parts: list[str] = []
        if spec.auth_identity:
            parts.append(f"identity:{spec.auth_identity}")
        if spec.auth_provider_name:
            parts.append(f"provider:{spec.auth_provider_name}")
        if self.auth_provider is not None:
            cache_identity = getattr(self.auth_provider, "cache_identity_for", None)
            if callable(cache_identity):
                value = cache_identity(spec)
                if isinstance(value, str) and value:
                    parts.append(f"provider_identity:{value}")
                else:
                    parts.append(f"provider_instance:{id(self.auth_provider)}")
            else:
                parts.append(f"provider_instance:{id(self.auth_provider)}")
        return "|".join(parts) if parts else None

    def _emit_approval_required(
        self,
        definition: MCPToolDefinition,
        reason: str | None,
    ) -> None:
        policy_metadata = self._policy_metadata(definition)
        data = {
            "server": definition.server,
            "server_label": definition.server,
            "tool": definition.name,
            "name": definition.name,
            "ref": definition.ref,
            "reason": reason,
            "scopes": policy_metadata["scopes"],
            "risks": policy_metadata["risks"],
            "trust_level": policy_metadata["trust_level"],
            "route_mode": policy_metadata["route_mode"],
            "fingerprint": policy_metadata["fingerprint"],
        }
        self._emit(EventTypes.MCP_APPROVAL_REQUIRED, data=data)
        self._emit(
            EventTypes.APPROVAL_REQUESTED,
            data={
                "approval_id": f"approval_{definition.ref}",
                "action": definition.ref,
                "arguments": {},
                "reason": reason,
                "metadata": policy_metadata,
            },
        )

    def _policy_metadata(self, definition: MCPToolDefinition) -> dict[str, Any]:
        spec = self._server_spec(definition.server)
        trust = definition.metadata.get("trust")
        trust_info = trust if isinstance(trust, dict) else {}
        scopes = _string_list(
            trust_info.get("required_scopes"),
            definition.metadata.get("required_scopes"),
            definition.metadata.get("scopes"),
        )
        risks = _string_list(trust_info.get("risks"), definition.metadata.get("risks"))
        return {
            "server": definition.server,
            "server_label": definition.server,
            "tool": definition.name,
            "name": definition.name,
            "ref": definition.ref,
            "transport": spec.transport,
            "scopes": scopes,
            "risks": risks,
            "read_only": definition.metadata.get("read_only"),
            "destructive": definition.metadata.get("destructive"),
            "trust_level": trust_info.get("trust_level"),
            "route_mode": trust_info.get("route_mode"),
            "approval_required": trust_info.get("approval_required"),
            "fingerprint": trust_info.get("fingerprint") or trust_fingerprint(spec),
        }

    def _tool_policy_for(
        self,
        spec: MCPServerSpec,
        name: str,
    ) -> Any | None:
        return spec.tool_policies.get(f"mcp:{spec.name}.{name}") or spec.tool_policies.get(name)

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
        spec = self._server_spec(definition.server)
        trust = definition.metadata.get("trust")
        max_output_bytes: int | None = None
        if isinstance(trust, dict) and isinstance(trust.get("max_output_bytes"), int):
            max_output_bytes = int(trust["max_output_bytes"])
        if max_output_bytes is None:
            max_output_bytes = effective_server_trust_policy(spec).max_output_bytes
        if max_output_bytes is None:
            return result
        rendered = result.rendered_content()
        encoded = rendered.encode("utf-8")
        if len(encoded) <= max_output_bytes:
            return result
        truncated = encoded[:max_output_bytes].decode("utf-8", errors="ignore")
        self._emit(
            EventTypes.MCP_OUTPUT_TRUNCATED,
            data={
                "server": definition.server,
                "server_label": definition.server,
                "tool": definition.name,
                "name": definition.name,
                "ref": definition.ref,
                "original_bytes": len(encoded),
                "max_output_bytes": max_output_bytes,
            },
        )
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

    def _apply_trust_metadata(
        self,
        definition: MCPToolDefinition,
        result: MCPCallResult,
    ) -> MCPCallResult:
        trust = definition.metadata.get("trust")
        trust_info = trust if isinstance(trust, dict) else {}
        trust_level = str(trust_info.get("trust_level") or "untrusted")
        spec = self._server_spec(definition.server)
        taint = default_taint_for_tool(
            definition,
            transport=spec.transport,
            trust_level=(
                self._server_decisions[definition.server].trust_level
                if definition.server in self._server_decisions
                else effective_server_trust_policy(spec).trust_level
            ),
        )
        existing_mcp_metadata = result.metadata.get("mcp")
        mcp_metadata = (
            dict(existing_mcp_metadata) if isinstance(existing_mcp_metadata, dict) else {}
        )
        metadata = {
            **result.metadata,
            "mcp": {
                **mcp_metadata,
                "server": definition.server,
                "tool": definition.name,
                "ref": definition.ref,
                "trust_level": trust_level,
                "route_mode": trust_info.get("route_mode"),
                "taint": taint.value,
                "output_truncated": bool(result.metadata.get("truncated")),
                "redacted": bool(spec.redact),
                "fingerprint": trust_info.get("fingerprint") or trust_fingerprint(spec),
            },
        }
        return MCPCallResult(
            server=result.server,
            tool=result.tool,
            ref=result.ref,
            content=result.content,
            structured_content=result.structured_content,
            is_error=result.is_error,
            raw=result.raw,
            metadata=metadata,
        )


def _string_list(*values: Any) -> list[str]:
    items: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            items.add(value)
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            items.update(str(item) for item in value)
    return sorted(items)


def _safe_error_message(exc: Exception, spec: MCPServerSpec) -> str:
    if isinstance(exc, MCPAuthenticationError):
        status = exc.status_code or "unknown"
        return (
            f"HTTP MCP authentication failed for server '{spec.name}' "
            f"with status {status}."
        )
    message = str(exc)
    if not spec.redact:
        return message
    redacted = _redact_known_secret_values(message, spec)
    if redacted != message:
        return redacted
    return f"{type(exc).__name__}: <redacted>"


def _redact_known_secret_values(message: str, spec: MCPServerSpec) -> str:
    redacted = message
    values: list[str] = []
    if spec.authorization:
        values.append(spec.authorization)
    for value in spec.headers.values():
        values.append(value)
    for value in spec.env.values():
        values.append(value)
    if spec.auth_identity:
        values.append(spec.auth_identity)
    for value in values:
        if value:
            redacted = redacted.replace(value, "<redacted>")
    return redacted
