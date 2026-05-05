from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from blackbox.mcp import (
    MCPApprovalMode,
    MCPConnector,
    MCPServer,
    MCPServerResult,
    MCPServerSpec,
    MCPServerTrustPolicy,
    MCPToolError,
    MCPTrustLevel,
    mcp_json_result,
    mcp_text_result,
)


class AddArgs(BaseModel):
    left: int = Field(ge=0)
    right: int = Field(ge=0)


def test_mcp_server_registers_pydantic_tool_schema() -> None:
    server = MCPServer("calc")

    @server.tool(
        description="Add two numbers.",
        input_model=AddArgs,
        annotations={"readOnlyHint": True},
    )
    def add(args: AddArgs) -> dict[str, int]:
        return {"total": args.left + args.right}

    tools = server.list_tools()

    assert tools[0]["name"] == "add"
    assert tools[0]["description"] == "Add two numbers."
    assert tools[0]["inputSchema"]["properties"]["left"]["minimum"] == 0
    assert tools[0]["inputSchema"]["required"] == ["left", "right"]
    assert tools[0]["annotations"] == {"readOnlyHint": True}


async def test_mcp_server_validates_pydantic_arguments() -> None:
    server = MCPServer("calc")

    @server.tool(input_model=AddArgs)
    def add(args: AddArgs) -> dict[str, int]:
        return {"total": args.left + args.right}

    result = await server.call_tool_result("add", {"left": -1, "right": 2})

    assert result.is_error is True
    assert result.structured_content["error"] == "invalid_arguments"
    assert result.structured_content["category"] == "ValidationError"
    assert result.structured_content["data"]["errors"][0]["loc"] == ["left"]


async def test_mcp_server_normalizes_sync_async_and_error_results() -> None:
    server = MCPServer("demo")

    @server.tool(input_schema={"type": "object", "properties": {"name": {"type": "string"}}})
    def greet(name: str) -> str:
        return f"Hello {name}"

    @server.tool()
    async def structured() -> MCPServerResult:
        return mcp_json_result({"ok": True})

    @server.tool()
    def fails() -> None:
        raise MCPToolError("provider_blocked", "Provider refused the request.", category="auth")

    text = await server.call_tool_result("greet", {"name": "Maia"})
    structured_result = await server.call_tool_result("structured")
    failed = await server.call_tool_result("fails")

    assert text.content == [{"type": "text", "text": "Hello Maia"}]
    assert structured_result.structured_content == {"ok": True}
    assert failed.is_error is True
    assert failed.structured_content == {
        "error": "provider_blocked",
        "message": "Provider refused the request.",
        "category": "auth",
    }


async def test_mcp_server_handles_jsonrpc_initialize_list_and_call() -> None:
    server = MCPServer("demo", instructions="Use carefully.")

    @server.tool(input_model=AddArgs)
    def add(args: AddArgs) -> dict[str, int]:
        return {"total": args.left + args.right}

    initialize = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }
    )
    tools = await server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    call = await server.handle_line(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "add", "arguments": {"left": 2, "right": 3}},
            }
        )
    )

    assert initialize["result"]["serverInfo"] == {"name": "demo", "version": "0.1.0"}
    assert initialize["result"]["instructions"] == "Use carefully."
    assert tools["result"]["tools"][0]["name"] == "add"
    assert call["result"]["structuredContent"] == {"total": 5}


async def test_mcp_server_ignores_initialized_notification() -> None:
    server = MCPServer("demo")

    response = await server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})

    assert response is None


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (mcp_text_result("done"), {"content": [{"type": "text", "text": "done"}]}),
        (
            mcp_json_result({"done": True}),
            {
                "content": [{"type": "text", "text": '{"done": true}'}],
                "structuredContent": {"done": True},
            },
        ),
    ],
)
def test_mcp_server_result_protocol_shape(result: MCPServerResult, expected: dict[str, object]) -> None:
    assert result.to_protocol() == expected


async def test_mcp_server_stdio_is_compatible_with_blackbox_connector(tmp_path: Path) -> None:
    script = tmp_path / "calc_server.py"
    script.write_text(
        """
from pydantic import BaseModel, Field

from blackbox.mcp import MCPServer


class AddArgs(BaseModel):
    left: int = Field(ge=0)
    right: int = Field(ge=0)


server = MCPServer("calc")


@server.tool(description="Add two numbers.", input_model=AddArgs)
def add(args: AddArgs) -> dict[str, int]:
    return {"total": args.left + args.right}


raise SystemExit(server.run_stdio())
""".lstrip(),
        encoding="utf-8",
    )
    source_root = Path.cwd() / "src"
    connector = MCPConnector(
        [
            MCPServerSpec(
                name="calc",
                transport="stdio",
                command=sys.executable,
                args=[str(script)],
                env={**os.environ, "PYTHONPATH": str(source_root)},
                trust_policy=MCPServerTrustPolicy(
                    server="calc",
                    trust_level=MCPTrustLevel.TRUSTED,
                    allowed_tools=frozenset({"add"}),
                    approval_mode=MCPApprovalMode.NEVER,
                ),
            )
        ]
    )

    try:
        tools = await connector.list_tools()
        result = await connector.call_tool_result("calc", "add", {"left": 2, "right": 5})
    finally:
        await connector.stop()

    assert tools[0]["ref"] == "mcp:calc.add"
    assert tools[0]["parameters"]["properties"]["left"]["minimum"] == 0
    assert result.structured_content == {"total": 7}
