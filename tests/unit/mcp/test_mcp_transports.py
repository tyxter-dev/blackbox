from __future__ import annotations

from typing import Any

import pytest

from blackbox.core.errors import MCPError
from blackbox.mcp import MCPServerSpec, StdioMCPTransport


class _TimeoutStdioTransport(StdioMCPTransport):
    def __init__(self, spec: MCPServerSpec) -> None:
        super().__init__(spec=spec)
        self.notifications: list[tuple[str, dict[str, Any] | None]] = []

    async def start(self) -> None:
        return None

    async def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.notifications.append((method, params))

    async def _write_payload(self, payload: dict[str, Any]) -> None:
        return None


async def test_stdio_transport_timeout_sends_cancellation_notification() -> None:
    transport = _TimeoutStdioTransport(
        MCPServerSpec(
            name="tickets",
            transport="stdio",
            command="fake",
            timeout_seconds=0.001,
        )
    )

    with pytest.raises(MCPError, match="timed out"):
        await transport.request("tools/list")

    method, params = transport.notifications[0]
    assert method == "notifications/cancelled"
    assert params is not None
    assert params["reason"] == "timeout"
