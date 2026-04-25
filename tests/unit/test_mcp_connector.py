from __future__ import annotations

import pytest

from agent_runtime.core.errors import ApprovalError, MCPError
from agent_runtime.core.events import EventTypes
from agent_runtime.core.policy import PolicyDecision, PolicyRequest
from agent_runtime.mcp import MCPConnector, MCPServerSpec
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


async def test_mcp_connector_lists_namespaced_tools() -> None:
    connector = MCPConnector([MCPServerSpec(name="tickets", transport="stdio")])
    connector.register_tool("tickets", "lookup", lambda ticket_id: {"id": ticket_id})

    tools = await connector.list_tools()

    assert tools[0]["ref"] == "mcp:tickets.lookup"
    assert tools[0]["server"] == "tickets"
    assert [event.type for event in connector.drain_events()] == [
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
    assert connector.drain_events() == []


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
    assert [event.type for event in events] == [EventTypes.MCP_APPROVAL_REQUIRED]
    assert events[0].data["ref"] == "mcp:tickets.lookup"


async def test_mcp_connector_discovers_and_calls_managed_transport() -> None:
    transport = _FakeTransport()
    connector = MCPConnector(
        [MCPServerSpec(name="tickets", transport="stdio", command="fake")],
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
        EventTypes.MCP_LIST_TOOLS_COMPLETED,
        EventTypes.MCP_CALL_STARTED,
        EventTypes.MCP_CALL_COMPLETED,
    ]


async def test_mcp_connector_refreshes_cache_and_stops_transport() -> None:
    transport = _FakeTransport()
    connector = MCPConnector(
        [MCPServerSpec(name="tickets", transport="stdio", command="fake")],
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
        [MCPServerSpec(name="tickets", transport="stdio", command="fake")],
        transports={"tickets": transport},
    )
    registry = ToolRegistry()

    definitions = await connector.register_runtime_tools(registry)
    result = await ToolRuntime(registry).call("mcp:tickets.lookup", {"ticket_id": "T-2"})

    assert definitions[0].name == "mcp:tickets.lookup"
    assert definitions[0].metadata["mcp"] is True
    assert result.payload == {"content": [{"type": "text", "text": {"ticket_id": "T-2"}}]}
