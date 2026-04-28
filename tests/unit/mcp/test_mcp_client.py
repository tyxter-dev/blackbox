from __future__ import annotations

import pytest

from agent_runtime.core.errors import MCPError
from agent_runtime.mcp import MCPClient, MCPServerSpec


class _Transport:
    def __init__(self, protocol_version: str = "2025-11-25") -> None:
        self.protocol_version = protocol_version
        self.requests: list[tuple[str, dict[str, object] | None]] = []
        self.notifications: list[str] = []
        self.started = False
        self.stopped = False

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
                "protocolVersion": self.protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake"},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "lookup",
                        "input_schema": {"type": "object"},
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            }
        if method == "tools/call":
            return {"content": [{"type": "text", "text": "ok"}]}
        raise AssertionError(method)

    async def notify(
        self,
        method: str,
        params: dict[str, object] | None = None,
    ) -> None:
        self.notifications.append(method)


async def test_start_initializes_and_sends_initialized_notification() -> None:
    transport = _Transport()
    client = MCPClient(
        spec=MCPServerSpec(name="tickets", transport="stdio", command="fake"),
        transport=transport,
    )

    session = await client.start()

    assert session.protocol_version == "2025-11-25"
    assert session.server_capabilities == {"tools": {}}
    assert transport.requests[0][0] == "initialize"
    assert transport.notifications == ["notifications/initialized"]


async def test_unsupported_protocol_version_raises_and_stops() -> None:
    transport = _Transport(protocol_version="1900-01-01")
    client = MCPClient(
        spec=MCPServerSpec(name="tickets", transport="stdio", command="fake"),
        transport=transport,
    )

    with pytest.raises(MCPError, match="unsupported protocol"):
        await client.start()

    assert transport.stopped is True


async def test_tools_list_normalizes_input_schema_and_annotations() -> None:
    transport = _Transport()
    client = MCPClient(
        spec=MCPServerSpec(name="tickets", transport="stdio", command="fake"),
        transport=transport,
    )

    tools = await client.list_tools()

    assert tools[0]["inputSchema"] == {"type": "object"}
    assert tools[0]["metadata"]["annotations"] == {"readOnlyHint": True}
