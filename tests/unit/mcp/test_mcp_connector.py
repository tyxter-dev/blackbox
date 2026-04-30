from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from agent_runtime.core.errors import ApprovalError, MCPError
from agent_runtime.core.events import EventTypes
from agent_runtime.core.policy import PolicyDecision, PolicyRequest
from agent_runtime.mcp import (
    MCPApprovalMode,
    MCPCapabilityRisk,
    MCPConnector,
    MCPServerSpec,
    MCPServerTrustPolicy,
    MCPToolTrustPolicy,
    MCPTrustLevel,
)
from agent_runtime.mcp.cache import MCPToolCacheEntry
from agent_runtime.tools.registry import ToolRegistry
from agent_runtime.tools.runtime import ToolRuntime


class _FakeTransport:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.requests: list[tuple[str, dict[str, object] | None]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def request(
        self,
        method: str,
        params: dict[str, object] | None = None,
    ) -> object:
        self.requests.append((method, params))
        if method == "initialize":
            return {"serverInfo": {"name": "fake"}}
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "lookup",
                        "description": "Lookup a ticket.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"ticket_id": {"type": "string"}},
                            "required": ["ticket_id"],
                        },
                    }
                ]
            }
        if method == "tools/call":
            assert params is not None
            return {"content": [{"type": "text", "text": params["arguments"]}]}
        raise AssertionError(method)


class _ListChangedTransport(_FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.tool_names = ["lookup"]
        self.notification_handler: Callable[[str, dict[str, Any] | None], None] | None = None

    def set_notification_handler(
        self,
        handler: Callable[[str, dict[str, Any] | None], None],
    ) -> None:
        self.notification_handler = handler

    async def request(
        self,
        method: str,
        params: dict[str, object] | None = None,
    ) -> object:
        if method == "tools/list":
            self.requests.append((method, params))
            return {
                "tools": [
                    {
                        "name": name,
                        "description": f"{name} tool.",
                        "inputSchema": {"type": "object"},
                    }
                    for name in self.tool_names
                ]
            }
        return await super().request(method, params)

    def emit_list_changed(self) -> None:
        if self.notification_handler is None:
            raise AssertionError("notification handler was not registered")
        self.notification_handler("notifications/tools/list_changed", None)


def test_mcp_server_spec_represents_prd_transport_names() -> None:
    specs = [
        MCPServerSpec(name="local", transport="stdio", command="mcp-server"),
        MCPServerSpec(name="http-api", transport="http", url="https://example.test/mcp"),
        MCPServerSpec(name="events", transport="sse", url="https://example.test/sse"),
        MCPServerSpec(
            name="stream",
            transport="streamable_http",
            url="https://example.test/stream",
        ),
    ]

    assert {spec.transport for spec in specs} == {
        "stdio",
        "http",
        "sse",
        "streamable_http",
    }


class _ScriptedPolicy:
    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision
        self.seen: list[PolicyRequest] = []

    async def check(self, request: PolicyRequest) -> PolicyDecision:
        self.seen.append(request)
        return self.decision


def _trusted_ticket_spec(**overrides: object) -> MCPServerSpec:
    return MCPServerSpec(
        name="tickets",
        transport="stdio",
        command="fake",
        trust_policy=MCPServerTrustPolicy(
            server="tickets",
            trust_level=MCPTrustLevel.TRUSTED,
            allowed_tools=frozenset({"lookup"}),
            approval_mode=MCPApprovalMode.NEVER,
        ),
        **overrides,
    )


async def test_mcp_connector_lists_namespaced_tools() -> None:
    connector = MCPConnector([MCPServerSpec(name="tickets", transport="stdio")])
    connector.register_tool("tickets", "lookup", lambda ticket_id: {"id": ticket_id})

    tools = await connector.list_tools()

    assert tools[0]["ref"] == "mcp:tickets.lookup"
    assert tools[0]["server"] == "tickets"
    assert [event.type for event in connector.drain_events()] == [
        EventTypes.MCP_LIST_TOOLS_STARTED,
        EventTypes.MCP_LIST_TOOLS_COMPLETED
    ]


async def test_mcp_connector_calls_registered_tool_and_emits_events() -> None:
    connector = MCPConnector([MCPServerSpec(name="tickets", transport="stdio")])

    async def lookup(ticket_id: str) -> dict[str, str]:
        return {"id": ticket_id, "status": "open"}

    connector.register_tool("tickets", "lookup", lookup)

    result = await connector.call_tool("tickets", "lookup", {"ticket_id": "T-1"})

    assert result == {"id": "T-1", "status": "open"}
    assert [event.type for event in connector.drain_events()] == [
        EventTypes.MCP_CALL_STARTED,
        EventTypes.MCP_CALL_COMPLETED,
    ]


async def test_mcp_connector_gates_calls_with_policy() -> None:
    policy = _ScriptedPolicy(PolicyDecision.deny("blocked"))
    connector = MCPConnector(
        [MCPServerSpec(name="tickets", transport="stdio")],
        policy=policy,
    )
    connector.register_tool("tickets", "lookup", lambda ticket_id: {"id": ticket_id})

    with pytest.raises(MCPError):
        await connector.call_tool("tickets", "mcp:tickets.lookup", {"ticket_id": "T-1"})

    assert policy.seen[0].checkpoint == "before_mcp_call"
    assert policy.seen[0].action == "mcp:tickets.lookup"
    assert [event.type for event in connector.drain_events()] == [
        EventTypes.MCP_CALL_FAILED
    ]


async def test_mcp_policy_request_includes_scope_risk_and_trust_metadata() -> None:
    policy = _ScriptedPolicy(PolicyDecision.deny("blocked"))
    spec = _trusted_ticket_spec(
        tool_policies={
            "lookup": MCPToolTrustPolicy(
                ref="lookup",
                trust_level=MCPTrustLevel.TRUSTED,
                risks=frozenset({MCPCapabilityRisk.EXTERNAL_AUTH}),
                required_scopes=frozenset({"tickets.read"}),
                approval_mode=MCPApprovalMode.NEVER,
            )
        }
    )
    connector = MCPConnector(
        [spec],
        policy=policy,
        transports={"tickets": _FakeTransport()},
    )

    with pytest.raises(MCPError):
        await connector.call_tool("tickets", "lookup", {"ticket_id": "T-1"})

    metadata = policy.seen[0].metadata
    assert metadata["server"] == "tickets"
    assert metadata["tool"] == "lookup"
    assert metadata["ref"] == "mcp:tickets.lookup"
    assert metadata["scopes"] == ["tickets.read"]
    assert metadata["risks"] == ["external_auth"]
    assert metadata["trust_level"] == "trusted"
    assert metadata["route_mode"] == "local_only"
    assert isinstance(metadata["fingerprint"], str)


async def test_mcp_connector_surfaces_required_approval() -> None:
    policy = _ScriptedPolicy(PolicyDecision.require_approval("review required"))
    connector = MCPConnector(
        [MCPServerSpec(name="tickets", transport="stdio")],
        policy=policy,
    )
    connector.register_tool("tickets", "lookup", lambda ticket_id: {"id": ticket_id})

    with pytest.raises(ApprovalError):
        await connector.call_tool("tickets", "lookup", {"ticket_id": "T-1"})

    events = connector.drain_events()
    assert [event.type for event in events] == [
        EventTypes.MCP_APPROVAL_REQUIRED,
        EventTypes.APPROVAL_REQUESTED,
    ]
    assert events[0].data["ref"] == "mcp:tickets.lookup"


async def test_mcp_connector_discovers_and_calls_managed_transport() -> None:
    transport = _FakeTransport()
    connector = MCPConnector(
        [_trusted_ticket_spec()],
        transports={"tickets": transport},
    )

    tools = await connector.list_tools("tickets")
    result = await connector.call_tool("tickets", "lookup", {"ticket_id": "T-1"})

    assert transport.started is True
    assert tools[0]["ref"] == "mcp:tickets.lookup"
    assert result == {"content": [{"type": "text", "text": {"ticket_id": "T-1"}}]}
    assert [request[0] for request in transport.requests] == [
        "initialize",
        "tools/list",
        "tools/call",
    ]
    assert [event.type for event in connector.drain_events()] == [
        EventTypes.MCP_LIST_TOOLS_STARTED,
        EventTypes.MCP_SERVER_VALIDATED,
        EventTypes.MCP_TRUST_EVALUATED,
        EventTypes.MCP_TOOLS_CACHE_MISS,
        EventTypes.MCP_TOOLS_DISCOVERED,
        EventTypes.MCP_TOOLS_FILTERED,
        EventTypes.MCP_LIST_TOOLS_COMPLETED,
        EventTypes.MCP_TOOLS_CACHE_HIT,
        EventTypes.MCP_CALL_STARTED,
        EventTypes.MCP_CALL_COMPLETED,
    ]


async def test_mcp_connector_can_share_discovery_cache_between_instances() -> None:
    shared_cache: dict[str, MCPToolCacheEntry] = {}
    first_transport = _FakeTransport()
    first = MCPConnector(
        [_trusted_ticket_spec()],
        transports={"tickets": first_transport},
        _tool_cache=shared_cache,
    )
    await first.list_tools("tickets")

    second_transport = _FakeTransport()
    second = MCPConnector(
        [_trusted_ticket_spec()],
        transports={"tickets": second_transport},
        _tool_cache=shared_cache,
    )
    tools = await second.list_tools("tickets")
    result = await second.call_tool("tickets", "lookup", {"ticket_id": "T-2"})

    assert tools[0]["ref"] == "mcp:tickets.lookup"
    assert result == {"content": [{"type": "text", "text": {"ticket_id": "T-2"}}]}
    assert [request[0] for request in first_transport.requests].count("tools/list") == 1
    assert [request[0] for request in second_transport.requests] == [
        "initialize",
        "tools/call",
    ]
    assert EventTypes.MCP_TOOLS_CACHE_HIT in [
        event.type for event in second.drain_events()
    ]


async def test_mcp_discovery_cache_is_isolated_by_auth_identity() -> None:
    shared_cache: dict[str, MCPToolCacheEntry] = {}
    first_transport = _FakeTransport()
    first = MCPConnector(
        [_trusted_ticket_spec(auth_identity="tenant-a")],
        transports={"tickets": first_transport},
        _tool_cache=shared_cache,
    )
    await first.list_tools("tickets")

    second_transport = _FakeTransport()
    second = MCPConnector(
        [_trusted_ticket_spec(auth_identity="tenant-b")],
        transports={"tickets": second_transport},
        _tool_cache=shared_cache,
    )
    await second.list_tools("tickets")

    assert [request[0] for request in second_transport.requests] == [
        "initialize",
        "tools/list",
    ]
    assert EventTypes.MCP_TOOLS_CACHE_HIT not in [
        event.type for event in second.drain_events()
    ]


async def test_mcp_list_changed_notification_invalidates_discovery_cache() -> None:
    transport = _ListChangedTransport()
    connector = MCPConnector(
        [
            MCPServerSpec(
                name="tickets",
                transport="stdio",
                command="fake",
                trust_policy=MCPServerTrustPolicy(
                    server="tickets",
                    trust_level=MCPTrustLevel.TRUSTED,
                    allowed_tools=frozenset({"lookup", "create"}),
                    approval_mode=MCPApprovalMode.NEVER,
                ),
            )
        ],
        transports={"tickets": transport},
    )

    first = await connector.list_tools("tickets")
    second = await connector.list_tools("tickets")
    transport.tool_names = ["create"]
    transport.emit_list_changed()
    refreshed = await connector.list_tools("tickets")

    assert [tool["name"] for tool in first] == ["lookup"]
    assert [tool["name"] for tool in second] == ["lookup"]
    assert [tool["name"] for tool in refreshed] == ["create"]
    assert [request[0] for request in transport.requests].count("tools/list") == 2
    invalidated = [
        event
        for event in connector.drain_events()
        if event.type == EventTypes.MCP_TOOLS_CACHE_INVALIDATED
    ]
    assert invalidated[-1].data["reason"] == "list_changed"


async def test_mcp_connector_refreshes_cache_and_stops_transport() -> None:
    transport = _FakeTransport()
    connector = MCPConnector(
        [_trusted_ticket_spec()],
        transports={"tickets": transport},
    )

    await connector.list_tools("tickets")
    await connector.list_tools("tickets")
    await connector.refresh_tools("tickets")
    await connector.stop()

    assert [request[0] for request in transport.requests].count("tools/list") == 2
    assert transport.stopped is True


async def test_mcp_connector_registers_runtime_tool_bridge() -> None:
    transport = _FakeTransport()
    connector = MCPConnector(
        [_trusted_ticket_spec()],
        transports={"tickets": transport},
    )
    registry = ToolRegistry()

    definitions = await connector.register_runtime_tools(registry)
    result = await ToolRuntime(registry).call("mcp:tickets.lookup", {"ticket_id": "T-2"})

    assert definitions[0].name == "mcp:tickets.lookup"
    assert definitions[0].metadata["mcp"] is True
    assert result.payload == {
        "mcp": {
            "server": "tickets",
            "tool": "lookup",
            "ref": "mcp:tickets.lookup",
            "structured_content": None,
            "content": [{"type": "text", "text": {"ticket_id": "T-2"}}],
            "is_error": False,
        }
    }
