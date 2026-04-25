from __future__ import annotations

import pytest

from agent_runtime.core.errors import ApprovalError, MCPError
from agent_runtime.core.events import EventTypes
from agent_runtime.core.policy import PolicyDecision, PolicyRequest
from agent_runtime.mcp import MCPConnector, MCPServerSpec


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
