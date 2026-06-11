"""External MCP integration validated offline against a fake CRM server.

Tier 0 of the validation ladder in ``docs/USE_CASE_VALIDATION.md``: instead
of a real CRM SaaS (account + API key required), the runtime spawns
``examples/mcp_servers/fake_crm.py`` as a managed stdio MCP server. The full
real path is exercised — subprocess transport, JSON-RPC initialize,
``tools/list`` discovery, trust policy, namespaced dispatch, canonical MCP
events — with zero credentials.

The model is scripted, so the run is deterministic and offline:

1. The model calls ``mcp:fake-crm.lookup_customer`` and
   ``mcp:fake-crm.list_open_deals`` in one turn.
2. It books a follow-up via ``mcp:fake-crm.create_followup_task``.
3. It produces the final answer.

Run::

    python examples/mcp_toolset_fake_crm.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

bootstrap()

from blackbox import (
    AgentRuntime,
    EventTypes,
    MCPApprovalMode,
    MCPServerSpec,
    MCPServerTrustPolicy,
    MCPToolset,
    MCPTrustLevel,
)
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.events import AgentEvent
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest

FAKE_CRM_SERVER = Path(__file__).resolve().parent / "mcp_servers" / "fake_crm.py"


class ScriptedModel:
    """Deterministic model: replays queued turn generators."""

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


def _tool_calls(calls: list[tuple[str, str, dict[str, Any]]]):
    def turn(request: TurnRequest):
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        for call_id, name, arguments in calls:
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


def fake_crm_toolset() -> MCPToolset:
    """Managed stdio MCP toolset for the fake CRM, trusted for local dispatch."""

    spec = MCPServerSpec(
        name="fake-crm",
        transport="stdio",
        command=sys.executable,
        args=[str(FAKE_CRM_SERVER)],
        trust_policy=MCPServerTrustPolicy(
            server="fake-crm",
            trust_level=MCPTrustLevel.FIRST_PARTY,
            approval_mode=MCPApprovalMode.NEVER,
        ),
    )
    return MCPToolset(server=spec, mode="local")


async def main() -> None:
    scripted = ScriptedModel()
    scripted.queue(_tool_calls([
        ("c1", "mcp:fake-crm.lookup_customer", {"customer_id": "cus_042"}),
        ("c2", "mcp:fake-crm.list_open_deals", {"customer_id": "cus_042"}),
    ]))
    scripted.queue(_tool_calls([
        ("c3", "mcp:fake-crm.create_followup_task", {
            "customer_id": "cus_042",
            "title": "Send renovation proposal",
            "due_date": "2026-06-15",
        }),
    ]))
    scripted.queue(_final_text(
        "Helena Prado has 2 open deals; follow-up task scheduled for June 15."
    ))

    runtime = AgentRuntime()
    runtime.registry.register_model(scripted)

    result = await runtime.run(
        provider="scripted:crm-demo",
        input="Review customer cus_042 and schedule the proposal follow-up.",
        toolsets=[fake_crm_toolset()],
    )

    print("mcp lifecycle:")
    for event in result.events:
        if event.type in {
            EventTypes.MCP_SERVER_STARTED,
            EventTypes.MCP_TOOLS_DISCOVERED,
            EventTypes.MCP_CALL_COMPLETED,
            EventTypes.MCP_SERVER_STOPPED,
        }:
            summary = {
                key: event.data.get(key)
                for key in ("server", "name", "tool", "tool_count", "tools")
                if event.data.get(key) is not None
            }
            print(f"  {event.type:26}  {summary}")

    print("\nstructured content from the fake CRM:")
    for payload in result.payloads:
        mcp = (payload.payload or {}).get("mcp", {})
        structured = mcp.get("structured_content")
        if structured is not None:
            print(f"  {payload.tool_name}: {structured}")

    print(f"\nfinal output: {result.output}")


if __name__ == "__main__":
    asyncio.run(main())
