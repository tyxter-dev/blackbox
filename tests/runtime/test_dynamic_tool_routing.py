from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime import AgentResult, AgentRuntime, EventTypes, ToolRoutingSpec
from agent_runtime.core.events import AgentEvent
from agent_runtime.core.state import ProviderState
from agent_runtime.tools.routing import ToolBudget
from agent_runtime.workspaces import WorkspaceSpec
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn, tool_call_turn


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


async def test_runtime_dynamic_tools_selects_bounded_local_subset() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda path: f"read:{path}",
        name="read_notes",
        description="Read project notes.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        category="coding",
        tags=["read"],
    )
    runtime.tools.register(
        lambda: "deleted",
        name="delete_notes",
        description="Delete project notes.",
        risk="high",
        side_effects=["workspace_write"],
    )

    def first_turn(request: Any) -> Any:
        assert [tool["name"] for tool in request.tools] == ["read_notes"]
        yield from tool_call_turn(
            call_id="read_1",
            name="read_notes",
            arguments={"path": "notes.txt"},
        )(request)

    scripted.queue(first_turn)
    scripted.queue(text_only_turn("done"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="read the project notes",
        tools="dynamic",
        tool_routing=ToolRoutingSpec(
            mode="auto",
            budget=ToolBudget(max_visible_tools=1),
            include_categories=("coding",),
        ),
    )

    assert result.output == "done"
    assert any(
        event.type == EventTypes.TOOL_CALL_COMPLETED
        and event.data.get("name") == "read_notes"
        for event in result.events
    )
    assert result.metadata["tool_routing"]["plans"][0]["selected_names"] == ["read_notes"]


async def test_runtime_dynamic_routing_runs_before_each_model_turn() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda: "beta",
        name="alpha_lookup",
        description="Look up alpha records.",
        category="alpha",
    )
    runtime.tools.register(
        lambda: "beta",
        name="beta_lookup",
        description="Look up beta records.",
        category="beta",
    )

    def alpha_turn(request: Any) -> Any:
        assert [tool["name"] for tool in request.tools] == ["alpha_lookup"]
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="alpha",
            data={"call_id": "alpha", "name": "alpha_lookup", "arguments": {}},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    def beta_turn(request: Any) -> Any:
        assert [tool["name"] for tool in request.tools] == ["beta_lookup"]
        yield from text_only_turn("done")(request)

    scripted.queue(alpha_turn)
    scripted.queue(beta_turn)

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="alpha",
        tools="dynamic",
        tool_routing=ToolRoutingSpec(
            mode="auto",
            budget=ToolBudget(max_visible_tools=1),
            refresh_each_turn=True,
        ),
    )

    assert result.output == "done"
    plans = result.metadata["tool_routing"]["plans"]
    assert [plan["selected_names"] for plan in plans] == [
        ["alpha_lookup"],
        ["beta_lookup"],
    ]


async def test_unselected_tool_call_is_denied_without_late_bind() -> None:
    runtime, scripted = _runtime()
    called: list[str] = []

    def safe_tool() -> str:
        called.append("safe")
        return "safe"

    def extra_tool() -> str:
        called.append("extra")
        return "extra"

    runtime.tools.register(safe_tool, name="safe_tool", description="Safe")
    runtime.tools.register(extra_tool, name="extra_tool", description="Extra")

    scripted.queue(
        tool_call_turn(call_id="extra", name="extra_tool", arguments={})
    )
    scripted.queue(text_only_turn("done"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="safe",
        tools="dynamic",
        tool_routing=ToolRoutingSpec(
            mode="auto",
            budget=ToolBudget(max_visible_tools=1),
            always_include=("local:safe_tool",),
            allow_late_bind=False,
        ),
    )

    assert called == []
    assert result.output == "done"
    assert any(
        event.type == EventTypes.TOOL_CHOICE_REJECTED
        and event.data.get("reason") == "tool_not_in_selected_plan"
        for event in result.events
    )


async def test_known_tool_late_binds_when_enabled() -> None:
    runtime, scripted = _runtime()
    called: list[str] = []

    def safe_tool() -> str:
        called.append("safe")
        return "safe"

    def extra_tool() -> str:
        called.append("extra")
        return "extra"

    runtime.tools.register(safe_tool, name="safe_tool", description="Safe")
    runtime.tools.register(extra_tool, name="extra_tool", description="Extra")

    scripted.queue(
        tool_call_turn(call_id="extra", name="extra_tool", arguments={})
    )
    scripted.queue(text_only_turn("done"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="safe",
        tools="dynamic",
        tool_routing=ToolRoutingSpec(
            mode="auto",
            budget=ToolBudget(max_visible_tools=1),
            always_include=("local:safe_tool",),
            allow_late_bind=True,
        ),
    )

    assert called == ["extra"]
    assert result.output == "done"
    assert any(event.type == EventTypes.TOOL_ROUTING_LATE_BOUND for event in result.events)


async def test_dynamic_routing_selects_workspace_read_tools(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("ship it")
    runtime, scripted = _runtime()

    def first_turn(request: Any) -> Any:
        names = [tool["name"] for tool in request.tools]
        assert "workspace_read_file" in names
        yield from tool_call_turn(
            call_id="read",
            name="workspace_read_file",
            arguments={"path": "notes.txt"},
        )(request)

    scripted.queue(first_turn)
    scripted.queue(text_only_turn("done"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="read notes",
        tools="dynamic",
        workspace=WorkspaceSpec.local(tmp_path),
    )

    assert result.output == "done"
    assert result.payloads[0].payload["content"] == "ship it"
    selected_refs = result.metadata["tool_routing"]["plans"][0]["selected_refs"]
    assert "workspace:read_file" in selected_refs
