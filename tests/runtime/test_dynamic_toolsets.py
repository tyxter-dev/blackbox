from __future__ import annotations

from typing import Any

from agent_runtime import AgentRuntime, EventTypes, ToolBudget, Toolset
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.policy import PolicyDecision, PolicyRequest
from agent_runtime.core.state import ProviderState
from agent_runtime.tools import ToolResult
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


class CRMTools(Toolset):
    def __init__(self) -> None:
        super().__init__(name="crm", description="CRM tools")
        self.register(
            lambda customer_id: ToolResult(content=f"customer:{customer_id}"),
            name="lookup_customer",
            description="Look up a customer record in the CRM.",
            parameters={
                "type": "object",
                "properties": {"customer_id": {"type": "string"}},
                "required": ["customer_id"],
            },
            category="crm",
            tags=["customer", "lookup"],
            risk="low",
            scopes=["crm:read"],
            latency="low",
            cost="low",
            examples=["Find the customer profile for a support issue."],
            negative_examples=["Do not use for billing invoices."],
        )


async def test_dynamic_toolset_loads_tools_between_model_turns() -> None:
    runtime, scripted = _runtime()

    def search_turn(request: Any) -> Any:
        names = [tool["name"] for tool in request.tools]
        assert names == ["search_tools", "load_tools"]
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="search_1",
            data={
                "call_id": "search_1",
                "name": "search_tools",
                "arguments": {"query": "customer crm"},
            },
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    def load_turn(request: Any) -> Any:
        names = [tool["name"] for tool in request.tools]
        assert names == ["search_tools", "load_tools"]
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="load_1",
            data={
                "call_id": "load_1",
                "name": "load_tools",
                "arguments": {"tool_names": ["lookup_customer"]},
            },
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    def call_loaded_turn(request: Any) -> Any:
        names = [tool["name"] for tool in request.tools]
        assert names == ["lookup_customer", "search_tools", "load_tools"]
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="crm_1",
            data={
                "call_id": "crm_1",
                "name": "lookup_customer",
                "arguments": {"customer_id": "cus_123"},
            },
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    scripted.queue(search_turn)
    scripted.queue(load_turn)
    scripted.queue(call_loaded_turn)
    scripted.queue(text_only_turn("Resolved customer cus_123."))

    result = await runtime.run(
        provider="scripted:test",
        input="Resolve this customer issue",
        toolsets=[CRMTools()],
        tool_selection="dynamic",
    )

    assert result.output == "Resolved customer cus_123."
    assert any(event.type == EventTypes.TOOL_SEARCH_COMPLETED for event in result.events)
    assert any(event.type == EventTypes.TOOL_CHOICE_LOADED for event in result.events)
    assert result.metadata["tool_choice"]["loaded"] == ["lookup_customer"]


class _ExposurePolicy:
    async def check(self, request: PolicyRequest) -> PolicyDecision:
        if request.checkpoint == "before_tool_exposure" and request.action == "dangerous":
            return PolicyDecision.deny("not allowed for this tenant")
        return PolicyDecision.allow()


async def test_policy_filters_tools_before_exposing_to_model() -> None:
    runtime, scripted = _runtime()
    toolset = Toolset(name="mixed")
    toolset.register(lambda: "ok", name="safe", description="Safe tool.")
    toolset.register(
        lambda: "boom",
        name="dangerous",
        description="Dangerous tool.",
        risk="high",
        side_effects=["external_write"],
    )

    def first_turn(request: Any) -> Any:
        assert [tool["name"] for tool in request.tools] == ["safe"]
        yield from text_only_turn("done")(request)

    scripted.queue(first_turn)

    result = await runtime.run(
        provider="scripted:test",
        input="use a tool",
        toolsets=[toolset],
        policy=_ExposurePolicy(),
    )

    assert result.output == "done"
    rejected = [
        event
        for event in result.events
        if event.type == EventTypes.TOOL_CHOICE_REJECTED
    ]
    assert rejected[0].data["name"] == "dangerous"
    assert rejected[0].data["reason"] == "not allowed for this tenant"


async def test_auto_tool_selection_defers_only_large_catalogs() -> None:
    runtime, scripted = _runtime()
    small = Toolset(name="small")
    small.register(lambda: "a", name="a", description="A tool.")
    small.register(lambda: "b", name="b", description="B tool.")

    def static_turn(request: Any) -> Any:
        assert [tool["name"] for tool in request.tools] == ["a", "b"]
        yield from text_only_turn("small done")(request)

    scripted.queue(static_turn)
    small_result = await runtime.run(
        provider="scripted:test",
        input="small catalog",
        toolsets=[small],
        tool_selection="auto",
        tool_budget=ToolBudget(large_catalog_threshold=10),
    )
    assert small_result.output == "small done"

    runtime, scripted = _runtime()
    large = Toolset(name="large")
    for index in range(3):
        large.register(
            lambda index=index: str(index),
            name=f"tool_{index}",
            description=f"Tool {index}.",
        )

    def dynamic_turn(request: Any) -> Any:
        assert [tool["name"] for tool in request.tools] == ["search_tools", "load_tools"]
        yield from text_only_turn("large done")(request)

    scripted.queue(dynamic_turn)
    large_result = await runtime.run(
        provider="scripted:test",
        input="large catalog",
        toolsets=[large],
        tool_selection="auto",
        tool_budget=ToolBudget(large_catalog_threshold=2),
    )
    assert large_result.output == "large done"


async def test_tool_budget_limits_visible_loads_and_parallel_calls() -> None:
    runtime, scripted = _runtime()
    toolset = Toolset(name="budgeted")
    toolset.register(lambda: "one", name="one", description="First tool.")
    toolset.register(lambda: "two", name="two", description="Second tool.")

    def load_turn(request: Any) -> Any:
        assert [tool["name"] for tool in request.tools] == ["search_tools", "load_tools"]
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="load_1",
            data={
                "call_id": "load_1",
                "name": "load_tools",
                "arguments": {"tool_names": ["one", "two"]},
            },
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    def loaded_turn(request: Any) -> Any:
        assert [tool["name"] for tool in request.tools] == ["one", "search_tools", "load_tools"]
        yield from text_only_turn("done")(request)

    scripted.queue(load_turn)
    scripted.queue(loaded_turn)

    result = await runtime.run(
        provider="scripted:test",
        input="load two tools",
        toolsets=[toolset],
        tool_selection="dynamic",
        tool_budget=ToolBudget(max_tools_visible=3, max_parallel_calls=1),
    )

    assert result.output == "done"
    rejected = [
        event
        for event in result.events
        if event.type == EventTypes.TOOL_CHOICE_REJECTED
        and event.data.get("reason") == "max_tools_visible_exceeded"
    ]
    assert rejected[0].data["name"] == "two"


async def test_tool_budget_limits_total_tool_calls() -> None:
    runtime, scripted = _runtime()
    calls: list[str] = []
    runtime.tools.register(lambda: calls.append("one") or "one", name="one", description="one")
    runtime.tools.register(lambda: calls.append("two") or "two", name="two", description="two")

    def two_call_turn(request: Any) -> Any:
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        for name in ["one", "two"]:
            yield AgentEvent(
                type=EventTypes.TOOL_CALL_REQUESTED,
                provider="scripted",
                item_id=f"{name}_1",
                data={"call_id": f"{name}_1", "name": name, "arguments": {}},
            )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    scripted.queue(two_call_turn)
    scripted.queue(text_only_turn("done"))

    result = await runtime.run(
        provider="scripted:test",
        input="call two tools",
        tools=["one", "two"],
        tool_budget=ToolBudget(max_tool_calls=1),
    )

    assert calls == ["one"]
    assert result.output == "done"
    rejected = [
        event
        for event in result.events
        if event.type == EventTypes.TOOL_CHOICE_REJECTED
        and event.data.get("reason") == "max_tool_calls_exceeded"
    ]
    assert rejected[0].data["name"] == "two"
