from __future__ import annotations

import asyncio

import pytest

from agent_runtime.core.errors import ToolExecutionError
from agent_runtime.tools import ToolRegistry, ToolResult, ToolRuntime


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
