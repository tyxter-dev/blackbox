from __future__ import annotations

import asyncio

import pytest

from agent_runtime.core.errors import ToolExecutionError
from agent_runtime.tools import ToolRegistry, ToolResult, ToolRuntime

# --- context injection ------------------------------------------------------

def greet(name: str, user_id: str) -> ToolResult:
    return ToolResult(content=f"hello {name}", payload={"user_id": user_id})


async def test_tool_runtime_injects_context_without_schema_visibility() -> None:
    registry = ToolRegistry()
    definition = registry.register(
        greet,
        name="greet",
        description="Greet a user.",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )

    runtime = ToolRuntime(registry, context={"user_id": "u_123"})
    result = await runtime.call("greet", {"name": "Diego"})

    assert result.content == "hello Diego"
    assert result.payload == {"user_id": "u_123"}
    provider_tools = registry.to_provider_tools()
    assert provider_tools[0]["parameters"] == definition.parameters
    assert "user_id" not in provider_tools[0]["parameters"]["properties"]


# --- error handling and result coercion -------------------------------------

def test_unknown_tool_raises_tool_execution_error() -> None:
    runtime = ToolRuntime(ToolRegistry())
    with pytest.raises(ToolExecutionError):
        runtime.registry.get("missing")


async def test_string_return_value_is_coerced_to_tool_result() -> None:
    registry = ToolRegistry()
    registry.register(lambda: "raw text", name="echo", description="e")
    result = await ToolRuntime(registry).call("echo")
    assert isinstance(result, ToolResult)
    assert result.content == "raw text"


async def test_arbitrary_return_value_becomes_payload() -> None:
    registry = ToolRegistry()
    registry.register(lambda: {"x": 1}, name="data", description="d")
    result = await ToolRuntime(registry).call("data")
    assert result.payload == {"x": 1}


# --- execution policies -----------------------------------------------------

async def test_timeout_raises() -> None:
    async def slow() -> str:
        await asyncio.sleep(0.5)
        return "late"

    registry = ToolRegistry()
    registry.register(slow, name="slow", description="s")
    runtime = ToolRuntime(registry, timeout=0.05)

    with pytest.raises(asyncio.TimeoutError):
        await runtime.call("slow")


async def test_blocking_tool_runs_in_thread() -> None:
    counter = {"n": 0}

    def blocking() -> str:
        counter["n"] += 1
        return "done"

    registry = ToolRegistry()
    registry.register(blocking, name="b", description="b", blocking=True)
    result = await ToolRuntime(registry).call("b")
    assert counter["n"] == 1
    assert result.content == "done"


async def test_concurrency_cap_serializes_calls() -> None:
    in_flight = {"now": 0, "max": 0}

    async def tick() -> str:
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        await asyncio.sleep(0.01)
        in_flight["now"] -= 1
        return "ok"

    registry = ToolRegistry()
    registry.register(tick, name="t", description="t")
    runtime = ToolRuntime(registry, max_concurrent=2)

    await runtime.call_many([("t", {}) for _ in range(6)])
    assert in_flight["max"] <= 2


async def test_mock_call_short_circuits_real_function() -> None:
    state = {"called": False}

    def real() -> ToolResult:
        state["called"] = True
        return ToolResult(content="real")

    registry = ToolRegistry()
    registry.register(real, name="real", description="r")
    result = await ToolRuntime(registry).call("real", mock=True)

    assert state["called"] is False
    assert "Mocked execution" in result.content
    assert result.metadata == {"mock": True, "tool_name": "real"}


def test_tool_registry_clone_is_isolated() -> None:
    registry = ToolRegistry()
    registry.register(lambda: "base", name="base", description="b")

    clone = registry.clone()
    clone.register(lambda: "dynamic", name="dynamic", description="d")

    assert [tool.name for tool in registry.all_tools()] == ["base"]
    assert sorted(tool.name for tool in clone.all_tools()) == ["base", "dynamic"]
