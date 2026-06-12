"""A generic external-API tool done safely: allowlist, timeout, size cap.

Mirrors the ``call_external_api`` pattern from ``docs/USE_CASE_VALIDATION.md``
(578/610 production agents carry a generic HTTP tool). The dangerous version
of this tool fetches arbitrary URLs into the model context; this one shows
the guardrails a real deployment needs:

- base-URL allowlist (the model picks an API *name*, never a raw URL),
- request timeout and response size cap,
- payload separation: parsed JSON goes to the application, a compact
  summary goes back to the model.

The HTTP request is real - a throwaway local server plays the external API,
so the example stays offline while exercising the genuine network path.

Run::

    python examples/external_api_tool.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import json
import threading
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from _bootstrap import bootstrap

bootstrap()

from blackbox import AgentRuntime, EventTypes
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.events import AgentEvent
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest
from blackbox.tools import ToolResult

TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 64_000


# --- a throwaway local "external API" -------------------------------------

ORDERS = {
    "ord_881": {"id": "ord_881", "status": "shipped", "carrier": "Correios",
                "tracking": "BR778812345", "eta": "2026-06-16"},
}


class _FakeAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        order_id = path.rsplit("/", 1)[-1]
        order = ORDERS.get(order_id)
        body = json.dumps(order if order else {"error": "order_not_found"}).encode()
        self.send_response(200 if order else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def start_fake_api() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAPIHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


# --- the safe tool ----------------------------------------------------------

API_ALLOWLIST: dict[str, str] = {}  # name -> base URL; filled in main()


def call_external_api(api: str, path: str, params: dict[str, Any] | None = None) -> ToolResult:
    base = API_ALLOWLIST.get(api)
    if base is None:
        return ToolResult(
            content=f"API {api!r} is not allowlisted. Available: {sorted(API_ALLOWLIST)}",
            metadata={"error": "api_not_allowlisted"},
        )
    url = f"{base}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    if not url.startswith(base + "/"):
        return ToolResult(content="Path escapes the allowlisted base URL.",
                          metadata={"error": "path_rejected"})
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception as exc:
        return ToolResult(content=f"Request failed: {type(exc).__name__}",
                          metadata={"error": "request_failed"})
    if len(raw) > MAX_RESPONSE_BYTES:
        return ToolResult(content="Response exceeded the size cap and was discarded.",
                          metadata={"error": "response_too_large"})
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ToolResult(content="Response was not valid JSON.",
                          metadata={"error": "invalid_json"})
    summary = ", ".join(f"{k}={v}" for k, v in list(data.items())[:6])
    return ToolResult(
        content=f"{api} {path} -> {summary}",   # compact summary for the model
        payload={"api": api, "path": path, "data": data},  # full JSON for the app
    )


# --- scripted run -----------------------------------------------------------

class ScriptedModel:
    provider_id = "scripted"

    def __init__(self) -> None:
        self._turns: list[Any] = []

    def queue(self, turn: Any) -> None:
        self._turns.append(turn)

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True, supports_function_tools=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        turn = self._turns.pop(0)
        for event in turn(request):
            yield event


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]):
    def turn(request: TurnRequest):
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id=call_id,
            data={"call_id": call_id, "name": name, "arguments": arguments},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    return turn


def _final_text(text: str):
    def turn(request: TurnRequest):
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA, provider="scripted", data={"delta": text}
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    return turn


async def main() -> None:
    API_ALLOWLIST["orders"] = start_fake_api()

    scripted = ScriptedModel()
    # The model tries a non-allowlisted API first, then the right one.
    scripted.queue(_tool_call("c1", "call_external_api",
                              {"api": "internal-admin", "path": "/v1/users"}))
    scripted.queue(_tool_call("c2", "call_external_api",
                              {"api": "orders", "path": "/v1/orders/ord_881"}))
    scripted.queue(_final_text(
        "Your order shipped with Correios (tracking BR778812345), arriving June 16."
    ))

    runtime = AgentRuntime()
    runtime.registry.register_model(scripted)
    runtime.tools.register(
        call_external_api,
        name="call_external_api",
        description="Call an allowlisted external API and return its JSON.",
    )

    result = await runtime.run(
        provider="scripted:support",
        input="Where is my order ord_881?",
        tools=["call_external_api"],
        tool_timeout=TIMEOUT_SECONDS + 1,
    )

    print("tool calls:")
    for event in result.events:
        if event.type == EventTypes.TOOL_CALL_COMPLETED:
            print(f"  completed: {event.data.get('name')}")

    print("\napplication payloads (full JSON, never inlined into the prompt):")
    for payload in result.payloads:
        if payload.payload:
            print(f"  {payload.payload['api']} {payload.payload['path']}: "
                  f"{payload.payload['data']}")

    print(f"\nagent reply: {result.output}")


if __name__ == "__main__":
    asyncio.run(main())
