from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.errors import MCPError
from agent_runtime.mcp.auth import MCPAuthProvider
from agent_runtime.mcp.spec import MCPServerSpec


@runtime_checkable
class MCPTransportClient(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any: ...


@dataclass(slots=True)
class JsonRPCTransport:
    """Base JSON-RPC request helper for MCP transports."""

    _next_id: int = field(default=1, init=False)

    async def _send_jsonrpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        request_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        response = await self._roundtrip(payload)
        if not isinstance(response, dict):
            raise MCPError("MCP transport returned a non-object JSON-RPC response.")
        if response.get("id") != request_id:
            raise MCPError("MCP transport returned a response for the wrong request id.")
        if "error" in response:
            raise MCPError(f"MCP JSON-RPC error: {response['error']!r}")
        return response.get("result")

    async def _roundtrip(self, payload: dict[str, Any]) -> Any:
        raise NotImplementedError


@dataclass(slots=True)
class StdioMCPTransport(JsonRPCTransport):
    spec: MCPServerSpec
    process: asyncio.subprocess.Process | None = None
    stderr_lines: list[str] = field(default_factory=list)
    _stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.process is not None:
            return
        if not self.spec.command:
            raise MCPError(f"stdio MCP server '{self.spec.name}' requires command.")
        self.process = await asyncio.create_subprocess_exec(
            self.spec.command,
            *self.spec.args,
            cwd=self.spec.cwd,
            env=dict(self.spec.env) if self.spec.env else None,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def stop(self) -> None:
        if self.process is None:
            return
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None
        self.process = None

    async def request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        await self.start()
        return await self._send_jsonrpc(method, params)

    async def _roundtrip(self, payload: dict[str, Any]) -> Any:
        if self.process is None or self.process.stdin is None or self.process.stdout is None:
            raise MCPError("stdio MCP transport is not running.")
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        self.process.stdin.write(encoded)
        await self.process.stdin.drain()
        line = await asyncio.wait_for(
            self.process.stdout.readline(),
            timeout=self.spec.timeout_seconds,
        )
        if not line:
            raise MCPError("stdio MCP server closed stdout.")
        try:
            return json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MCPError("stdio MCP server wrote non-JSON protocol output.") from exc

    async def _drain_stderr(self) -> None:
        if self.process is None or self.process.stderr is None:
            return
        while True:
            line = await self.process.stderr.readline()
            if not line:
                return
            self.stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())


@dataclass(slots=True)
class StreamableHTTPMCPTransport(JsonRPCTransport):
    spec: MCPServerSpec
    auth_provider: MCPAuthProvider | None = None
    session_id: str | None = None
    _started: bool = False

    async def start(self) -> None:
        if not self.spec.url:
            raise MCPError(f"HTTP MCP server '{self.spec.name}' requires url.")
        self._started = True

    async def stop(self) -> None:
        self._started = False
        self.session_id = None

    async def request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        await self.start()
        return await self._send_jsonrpc(method, params)

    async def _roundtrip(self, payload: dict[str, Any]) -> Any:
        auth_header = None
        if self.auth_provider is not None:
            auth_header = await self.auth_provider.authorization_header(self.spec)
        return await asyncio.to_thread(self._roundtrip_sync, payload, auth_header)

    def _roundtrip_sync(self, payload: dict[str, Any], auth_header: str | None) -> Any:
        if not self.spec.url:
            raise MCPError(f"HTTP MCP server '{self.spec.name}' requires url.")
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Method": str(payload.get("method", "")),
            **self.spec.headers,
        }
        name = _mcp_name_header(payload)
        if name is not None:
            headers["Mcp-Name"] = name
        if self.session_id is not None:
            headers["Mcp-Session-Id"] = self.session_id
        if auth_header is not None:
            headers["Authorization"] = auth_header
        request = urllib.request.Request(
            self.spec.url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.spec.timeout_seconds) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                response_body = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.URLError as exc:
            raise MCPError(f"HTTP MCP request failed: {exc}") from exc
        if "text/event-stream" in content_type:
            return _last_sse_json(response_body)
        return json.loads(response_body) if response_body else {}


@dataclass(slots=True)
class LegacySSEMCPTransport(StreamableHTTPMCPTransport):
    """Compatibility transport for pre-Streamable-HTTP MCP servers."""


async def iter_sse_json_lines(text: str) -> AsyncIterator[Any]:
    for event in _iter_sse_data(text):
        yield json.loads(event)


def _last_sse_json(text: str) -> Any:
    last: Any = {}
    for event in _iter_sse_data(text):
        last = json.loads(event)
    return last


def _iter_sse_data(text: str) -> list[str]:
    events: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line:
            if current:
                events.append("\n".join(current))
                current = []
            continue
        if line.startswith("data:"):
            current.append(line[5:].lstrip())
    if current:
        events.append("\n".join(current))
    return events


def _mcp_name_header(payload: dict[str, Any]) -> str | None:
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    value = params.get("name") or params.get("uri")
    return str(value) if value is not None else None


def transport_for_spec(
    spec: MCPServerSpec,
    *,
    auth_provider: MCPAuthProvider | None = None,
) -> MCPTransportClient:
    if spec.transport == "stdio":
        return StdioMCPTransport(spec=spec)
    if spec.transport == "sse":
        return LegacySSEMCPTransport(spec=spec, auth_provider=auth_provider)
    if spec.transport in {"http", "streamable_http"}:
        return StreamableHTTPMCPTransport(spec=spec, auth_provider=auth_provider)
    raise MCPError(f"Unsupported MCP transport: {spec.transport}")
