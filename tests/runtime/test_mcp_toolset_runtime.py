from __future__ import annotations

from typing import Any

from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.mcp import MCPServerSpec, MCPToolset
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn, tool_call_turn


class _FakeTransport:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.requests: list[tuple[str, dict[str, object] | None]] = []

    @property
    def session_id(self) -> str | None:
        return "session-1"

    @property
    def connected(self) -> bool:
        return self.started and not self.stopped

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

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
                "serverInfo": {"name": "fake"},
            }
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
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"ticket:{dict(params['arguments'])['ticket_id']}",
                    }
                ],
                "structuredContent": {"ok": True},
            }
        raise AssertionError(method)

    async def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.requests.append((method, params))


async def test_runtime_toolsets_expose_local_mcp_tools_and_stop_connector(monkeypatch: Any) -> None:
    import agent_runtime.mcp.connector as connector_module

    transport = _FakeTransport()
    monkeypatch.setattr(
        connector_module,
        "transport_for_spec",
        lambda spec, auth_provider=None: transport,
    )
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    scripted.queue(
        tool_call_turn(
            call_id="c1",
            name="mcp:tickets.lookup",
            arguments={"ticket_id": "T-1"},
        )
    )
    scripted.queue(text_only_turn("done"))

    result = await runtime.run(
        provider="scripted:test",
        input="lookup ticket",
        toolsets=[
            MCPToolset(
                server=MCPServerSpec(
                    name="tickets",
                    transport="stdio",
                    command="fake",
                ),
                mode="local",
            )
        ],
    )

    assert scripted.calls[0].tools[0]["name"] == "mcp:tickets.lookup"
    assert result.payloads[0].tool_name == "mcp:tickets.lookup"
    assert result.payloads[0].payload["mcp"]["structured_content"] == {"ok": True}
    assert EventTypes.MCP_CALL_COMPLETED in [event.type for event in result.events]
    assert transport.stopped is True
