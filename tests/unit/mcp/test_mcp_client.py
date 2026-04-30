from __future__ import annotations

import pytest

from blackbox.core.errors import MCPError
from blackbox.mcp import (
    SUPPORTED_MCP_PROTOCOL_VERSIONS,
    MCPClient,
    MCPServerSpec,
    mcp_protocol_matrix,
    supported_protocol_versions_for,
)


class _Transport:
    """In-memory MCP transport fake used to assert client initialization and tool calls."""

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


@pytest.mark.parametrize("protocol_version", SUPPORTED_MCP_PROTOCOL_VERSIONS)
async def test_start_negotiates_each_supported_protocol_version(
    protocol_version: str,
) -> None:
    transport = _Transport(protocol_version=protocol_version)
    spec = MCPServerSpec(
        name="tickets",
        transport="stdio",
        command="fake",
        protocol_version="2025-11-25",
        protocol_version_fallbacks=tuple(
            version
            for version in SUPPORTED_MCP_PROTOCOL_VERSIONS
            if version != "2025-11-25"
        ),
    )
    client = MCPClient(spec=spec, transport=transport)

    session = await client.start()

    assert session.protocol_version == protocol_version


def test_protocol_matrix_lists_spec_requested_versions() -> None:
    spec = MCPServerSpec(name="tickets", transport="stdio", command="fake")

    assert supported_protocol_versions_for(spec) == SUPPORTED_MCP_PROTOCOL_VERSIONS
    assert [entry.version for entry in mcp_protocol_matrix()] == list(
        SUPPORTED_MCP_PROTOCOL_VERSIONS
    )


async def test_unsupported_protocol_version_raises_and_stops() -> None:
    transport = _Transport(protocol_version="1900-01-01")
    client = MCPClient(
        spec=MCPServerSpec(name="tickets", transport="stdio", command="fake"),
        transport=transport,
    )

    with pytest.raises(MCPError, match="unsupported protocol"):
        await client.start()

    assert transport.stopped is True


async def test_requested_unsupported_protocol_version_raises_before_start() -> None:
    transport = _Transport()
    client = MCPClient(
        spec=MCPServerSpec(
            name="tickets",
            transport="stdio",
            command="fake",
            protocol_version="1900-01-01",
            protocol_version_fallbacks=(),
        ),
        transport=transport,
    )

    with pytest.raises(MCPError, match="no runtime-supported protocol"):
        await client.start()

    assert transport.started is False


async def test_tools_list_normalizes_input_schema_and_annotations() -> None:
    transport = _Transport()
    client = MCPClient(
        spec=MCPServerSpec(name="tickets", transport="stdio", command="fake"),
        transport=transport,
    )

    tools = await client.list_tools()

    assert tools[0]["inputSchema"] == {"type": "object"}
    assert tools[0]["metadata"]["annotations"] == {"readOnlyHint": True}
