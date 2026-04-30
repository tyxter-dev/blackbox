from __future__ import annotations

import pytest

from agent_runtime import EventTypes
from agent_runtime.core.errors import MCPError
from agent_runtime.mcp import (
    DefaultMCPTrustEvaluator,
    MCPApprovalMode,
    MCPConnector,
    MCPRouteMode,
    MCPServerSpec,
    MCPServerTrustPolicy,
    MCPTrustLevel,
    MCPTrustPolicyPresets,
)


class _TrustTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object] | None]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def request(
        self,
        method: str,
        params: dict[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> object:
        self.requests.append((method, params))
        if method == "initialize":
            return {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "trust-test"},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {"name": "lookup", "description": "Lookup a ticket."},
                    {
                        "name": "write_file",
                        "description": "Write a file.",
                        "annotations": {"destructiveHint": True},
                    },
                ]
            }
        raise AssertionError(method)


def test_unknown_server_defaults_to_untrusted() -> None:
    spec = MCPServerSpec(name="tickets", transport="stdio", command="tickets-mcp")
    decision = DefaultMCPTrustEvaluator().evaluate_server(spec, None)

    assert decision.allowed is True
    assert decision.trust_level == MCPTrustLevel.UNTRUSTED
    assert decision.approval_required is True


def test_trust_policy_presets_define_production_postures() -> None:
    local = MCPTrustPolicyPresets.local_only(
        "filesystem",
        allowed_tools=frozenset({"read_file"}),
    )
    enterprise = MCPTrustPolicyPresets.enterprise_remote(
        "github",
        allowed_domains=frozenset({"github.com"}),
        allowed_scopes=frozenset({"issues:read"}),
    )
    provider_native = MCPTrustPolicyPresets.provider_native_allowed(
        "github",
        allowed_domains=frozenset({"github.com"}),
    )

    assert local.route_mode == MCPRouteMode.LOCAL_ONLY
    assert local.allowed_tools == frozenset({"read_file"})
    assert local.approval_mode == MCPApprovalMode.HIGH_RISK_TOOL
    assert local.require_sandbox is True
    assert enterprise.trust_level == MCPTrustLevel.TRUSTED
    assert enterprise.allowed_domains == frozenset({"github.com"})
    assert enterprise.allowed_scopes == frozenset({"issues:read"})
    assert enterprise.route_mode == MCPRouteMode.LOCAL_ONLY
    assert provider_native.route_mode == MCPRouteMode.PROVIDER_NATIVE_ALLOWED
    assert provider_native.require_sandbox is False


async def test_blocked_server_cannot_start() -> None:
    spec = MCPServerSpec(
        name="tickets",
        transport="stdio",
        command="tickets-mcp",
        trust_policy=MCPServerTrustPolicy(
            server="tickets",
            trust_level=MCPTrustLevel.BLOCKED,
        ),
    )
    connector = MCPConnector([spec], transports={"tickets": _TrustTransport()})

    with pytest.raises(MCPError, match="blocked"):
        await connector.list_tools("tickets")

    assert EventTypes.MCP_SERVER_BLOCKED in [event.type for event in connector.drain_events()]


async def test_untrusted_discovered_tools_are_quarantined_by_default() -> None:
    connector = MCPConnector(
        [MCPServerSpec(name="tickets", transport="stdio", command="tickets-mcp")],
        transports={"tickets": _TrustTransport()},
    )

    tools = await connector.list_tools("tickets")
    events = connector.drain_events()

    assert tools == []
    assert EventTypes.MCP_TOOL_QUARANTINED in [event.type for event in events]
    filtered = next(event for event in events if event.type == EventTypes.MCP_TOOLS_FILTERED)
    assert filtered.data["quarantined_count"] == 2


async def test_allowlisted_tool_exposes_only_allowed_discovery_results() -> None:
    spec = MCPServerSpec(
        name="tickets",
        transport="stdio",
        command="tickets-mcp",
        trust_policy=MCPServerTrustPolicy(
            server="tickets",
            trust_level=MCPTrustLevel.REVIEWED,
            allowed_tools=frozenset({"lookup"}),
            approval_mode=MCPApprovalMode.NEVER,
        ),
    )
    connector = MCPConnector([spec], transports={"tickets": _TrustTransport()})

    tools = await connector.list_tools("tickets")

    assert [tool["name"] for tool in tools] == ["lookup"]
    assert tools[0]["metadata"]["trust"]["trust_level"] == "reviewed"
