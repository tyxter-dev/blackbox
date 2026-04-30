from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.errors import MCPAuthenticationError, MCPError
from agent_runtime.mcp.auth import (
    LegacyMCPAuthProvider,
    MCPAuthChallenge,
    MCPAuthProvider,
    auth_headers_for,
)
from agent_runtime.mcp.spec import MCPServerSpec


@runtime_checkable
class MCPTransportClient(Protocol):
    """Protocol implemented by MCP transports used by MCPClient."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        """Send a JSON-RPC request and return its result payload."""
        ...

    async def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Send a JSON-RPC notification without waiting for a response."""
        ...

    @property
    def session_id(self) -> str | None: ...

    @property
    def connected(self) -> bool: ...


@dataclass(slots=True)
class JsonRPCTransport:
    """Base JSON-RPC request helper for MCP transports."""

    _next_id: int = field(default=1, init=False)

    def _request_payload(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        request_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return request_id, payload

    @staticmethod
    def _notification_payload(
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return payload

    @staticmethod
    def _result_from_response(response: Any, request_id: int) -> Any:
        if not isinstance(response, dict):
            raise MCPError("MCP transport returned a non-object JSON-RPC response.")
        if response.get("id") != request_id:
            raise MCPError("MCP transport returned a response for the wrong request id.")
        if "error" in response:
            raise MCPError(f"MCP JSON-RPC error: {response['error']!r}")
        return response.get("result")


@dataclass(slots=True)
class StdioMCPTransport(JsonRPCTransport):
    spec: MCPServerSpec
    process: asyncio.subprocess.Process | None = None
    stderr_lines: list[str] = field(default_factory=list)
    _stdout_task: asyncio.Task[None] | None = None
    _stderr_task: asyncio.Task[None] | None = None
    _pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _notification_handler: Callable[[str, dict[str, Any] | None], None] | None = None

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def connected(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        if self.connected:
            return
        self.spec.validate_managed(allow_remote_http=True)
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
        self._stdout_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    def set_notification_handler(
        self,
        handler: Callable[[str, dict[str, Any] | None], None] | None,
    ) -> None:
        self._notification_handler = handler

    async def stop(self) -> None:
        """Shut down the stdio server process and fail pending requests.

        The shutdown path closes stdin first, then escalates from graceful wait
        to terminate and kill using the configured timeout.
        """
        process = self.process
        if process is None:
            return
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
            wait_closed = getattr(process.stdin, "wait_closed", None)
            if callable(wait_closed):
                try:
                    await wait_closed()
                except Exception:
                    pass
        if process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self.spec.shutdown_timeout_seconds,
                )
            except TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(
                        process.wait(),
                        timeout=self.spec.shutdown_timeout_seconds,
                    )
                except TimeoutError:
                    process.kill()
                    await process.wait()
        for task in (self._stdout_task, self._stderr_task):
            if task is not None:
                task.cancel()
        self._stdout_task = None
        self._stderr_task = None
        self.process = None
        self._fail_pending(MCPError("stdio MCP transport stopped."))

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        await self.start()
        request_id, payload = self._request_payload(method, params)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = future
        await self._write_payload(payload)
        timeout = timeout_seconds if timeout_seconds is not None else self.spec.timeout_seconds
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            await self.notify(
                "notifications/cancelled",
                {"requestId": request_id, "reason": "timeout"},
            )
            raise MCPError(f"MCP request timed out: {method}") from exc
        return self._result_from_response(response, request_id)

    async def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        await self.start()
        await self._write_payload(self._notification_payload(method, params))

    async def _write_payload(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise MCPError("stdio MCP transport is not running.")
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        async with self._write_lock:
            self.process.stdin.write(encoded)
            await self.process.stdin.drain()

    async def _read_stdout(self) -> None:
        if self.process is None or self.process.stdout is None:
            return
        while True:
            line = await self.process.stdout.readline()
            if not line:
                self._fail_pending(MCPError("stdio MCP server closed stdout."))
                return
            try:
                message = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as exc:
                self._fail_pending(
                    MCPError("stdio MCP server wrote non-JSON protocol output.")
                )
                raise MCPError("stdio MCP server wrote non-JSON protocol output.") from exc
            if not isinstance(message, dict):
                continue
            request_id = message.get("id")
            if isinstance(request_id, int):
                future = self._pending.pop(request_id, None)
                if future is not None and not future.done():
                    future.set_result(message)
                continue
            method = message.get("method")
            params = message.get("params")
            if isinstance(method, str) and self._notification_handler is not None:
                self._notification_handler(
                    method,
                    dict(params) if isinstance(params, dict) else None,
                )

    async def _drain_stderr(self) -> None:
        if self.process is None or self.process.stderr is None:
            return
        while True:
            line = await self.process.stderr.readline()
            if not line:
                return
            self.stderr_lines.append(line.decode("utf-8", errors="replace").rstrip())

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()


class _HTTPAuthRequiredError(Exception):
    def __init__(self, status_code: int, www_authenticate: str | None) -> None:
        self.status_code = status_code
        self.www_authenticate = www_authenticate
        super().__init__("MCP HTTP authentication required.")


@dataclass(slots=True)
class StreamableHTTPMCPTransport(JsonRPCTransport):
    """HTTP MCP transport with session, protocol, and auth challenge handling."""

    spec: MCPServerSpec
    auth_provider: MCPAuthProvider | LegacyMCPAuthProvider | None = None
    _session_id: str | None = None
    protocol_version: str | None = None
    _started: bool = False

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def connected(self) -> bool:
        return self._started

    def set_protocol_version(self, protocol_version: str) -> None:
        self.protocol_version = protocol_version

    async def start(self) -> None:
        self.spec.validate_managed(allow_remote_http=True)
        if not self.spec.url:
            raise MCPError(f"HTTP MCP server '{self.spec.name}' requires url.")
        self._started = True

    async def stop(self) -> None:
        self._started = False
        self._session_id = None
        self.protocol_version = None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        await self.start()
        request_id, payload = self._request_payload(method, params)
        timeout = timeout_seconds if timeout_seconds is not None else self.spec.timeout_seconds
        response = await self._roundtrip(payload, timeout_seconds=timeout)
        return self._result_from_response(response, request_id)

    async def notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        await self.start()
        await self._roundtrip(
            self._notification_payload(method, params),
            timeout_seconds=self.spec.timeout_seconds,
        )

    async def _roundtrip(self, payload: dict[str, Any], *, timeout_seconds: float) -> Any:
        auth_headers = await auth_headers_for(self.auth_provider, self.spec)
        try:
            return await asyncio.to_thread(
                self._roundtrip_sync,
                payload,
                auth_headers,
                timeout_seconds,
            )
        except _HTTPAuthRequiredError as exc:
            challenge = _auth_challenge_from_http(self.spec.name, exc)
            handler = getattr(self.auth_provider, "handle_auth_challenge", None)
            if not callable(handler):
                raise _authentication_error(self.spec, challenge, refreshed=False) from exc
            refreshed = await handler(self.spec, challenge)
            if refreshed is None:
                raise _authentication_error(self.spec, challenge, refreshed=False) from exc
            auth_headers.update(refreshed)
            try:
                return await asyncio.to_thread(
                    self._roundtrip_sync,
                    payload,
                    auth_headers,
                    timeout_seconds,
                )
            except _HTTPAuthRequiredError as refreshed_exc:
                refreshed_challenge = _auth_challenge_from_http(
                    self.spec.name,
                    refreshed_exc,
                )
                raise _authentication_error(
                    self.spec,
                    refreshed_challenge,
                    refreshed=True,
                ) from refreshed_exc

    def _roundtrip_sync(
        self,
        payload: dict[str, Any],
        auth_headers: dict[str, str],
        timeout_seconds: float,
    ) -> Any:
        if not self.spec.url:
            raise MCPError(f"HTTP MCP server '{self.spec.name}' requires url.")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Method": str(payload.get("method", "")),
            **self.spec.headers,
            **auth_headers,
        }
        name = _mcp_name_header(payload)
        if name is not None:
            headers["Mcp-Name"] = name
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        if self.protocol_version is not None:
            headers["MCP-Protocol-Version"] = self.protocol_version
        request = urllib.request.Request(
            self.spec.url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                response_body = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise _HTTPAuthRequiredError(
                    exc.code,
                    exc.headers.get("WWW-Authenticate"),
                ) from exc
            raise MCPError(f"HTTP MCP request failed with status {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise MCPError(f"HTTP MCP request failed: {exc.reason}") from exc
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


def _auth_challenge_from_http(
    server: str,
    error: _HTTPAuthRequiredError,
) -> MCPAuthChallenge:
    header = error.www_authenticate
    return MCPAuthChallenge(
        server=server,
        status_code=error.status_code,
        www_authenticate=header,
        resource_metadata_url=(
            _www_authenticate_param(header, "resource_metadata")
            or _www_authenticate_param(header, "resource")
        ),
        scope=_www_authenticate_param(header, "scope"),
    )


def _authentication_error(
    spec: MCPServerSpec,
    challenge: MCPAuthChallenge,
    *,
    refreshed: bool,
) -> MCPAuthenticationError:
    status = challenge.status_code
    return MCPAuthenticationError(
        "HTTP MCP authentication failed for "
        f"server '{spec.name}' with status {status}. "
        "Provide valid MCP credentials or configure an auth provider that can refresh them.",
        server=spec.name,
        status_code=status,
        www_authenticate=challenge.www_authenticate,
        resource_metadata_url=challenge.resource_metadata_url,
        scope=challenge.scope,
        safe_to_retry=not refreshed,
    )


def _www_authenticate_param(header: str | None, name: str) -> str | None:
    if not header:
        return None
    match = re.search(rf'{re.escape(name)}="?([^",]+)"?', header)
    return match.group(1) if match is not None else None


def transport_for_spec(
    spec: MCPServerSpec,
    *,
    auth_provider: MCPAuthProvider | LegacyMCPAuthProvider | None = None,
) -> MCPTransportClient:
    if spec.transport == "stdio":
        return StdioMCPTransport(spec=spec)
    if spec.transport == "sse":
        return LegacySSEMCPTransport(spec=spec, auth_provider=auth_provider)
    if spec.transport in {"http", "streamable_http"}:
        return StreamableHTTPMCPTransport(spec=spec, auth_provider=auth_provider)
    raise MCPError(f"Unsupported MCP transport: {spec.transport}")
