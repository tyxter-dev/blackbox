from __future__ import annotations

from agent_runtime.tools import ToolRegistry, ToolResult, ToolRuntime


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
