from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.errors import ApprovalError, MCPError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.policy import Policy, PolicyDecision, PolicyRequest
from agent_runtime.mcp.spec import MCPServerSpec

MCPToolCallable = Callable[..., Any]


@dataclass(slots=True, frozen=True)
class MCPToolDefinition:
    server: str
    name: str
    function: MCPToolCallable
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
            "name": self.name,
            "ref": self.ref,
            "description": self.description,
            "parameters": self.parameters,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class MCPConnector:
    """Minimal local MCP dispatch connector.

    This first slice does not open stdio or HTTP transports itself. It gives the
    runtime a typed connector surface for registering discovered local MCP tools,
    listing them with stable ``mcp:<server>.<tool>`` refs, policy-gating calls,
    and emitting the canonical MCP event taxonomy.
    """

    servers: list[MCPServerSpec]
    policy: Policy | None = None
    events: list[AgentEvent] = field(default_factory=list)
    _tools: dict[str, MCPToolDefinition] = field(default_factory=dict)

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
        tools = [
            tool.to_descriptor()
            for tool in self._tools.values()
            if server is None or tool.server == server
        ]
        self._emit(
            EventTypes.MCP_LIST_TOOLS_COMPLETED,
            data={"server": server, "tools": tools},
        )
        return tools

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        definition = self._resolve(server, tool)
        args = dict(arguments or {})
        await self._policy_gate(definition, args)
        self._emit(
            EventTypes.MCP_CALL_STARTED,
            data={"server": definition.server, "tool": definition.name, "ref": definition.ref},
        )
        result = definition.function(**args)
        if inspect.isawaitable(result):
            result = await result
        self._emit(
            EventTypes.MCP_CALL_COMPLETED,
            data={
                "server": definition.server,
                "tool": definition.name,
                "ref": definition.ref,
                "result": result,
            },
        )
        return result

    def drain_events(self) -> list[AgentEvent]:
        drained = list(self.events)
        self.events.clear()
        return drained

    def _resolve(self, server: str, tool: str) -> MCPToolDefinition:
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
                "tool": definition.name,
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
