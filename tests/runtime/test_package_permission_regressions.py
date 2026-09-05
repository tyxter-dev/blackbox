from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from blackbox import (
    AgentRuntime,
    RuntimeConfig,
    ToolPermission,
    ToolSearchControl,
    WorkspaceAgentSpec,
)
from blackbox.core.approvals import ApprovalDecision
from blackbox.core.errors import UnsupportedFeatureError
from blackbox.providers.model_adapters.openai_responses import OpenAIResponsesProvider
from blackbox.tools.routing import ToolRoutingSpec
from blackbox.workspace_agents.permissions import ApprovalRequirement
from blackbox.workspace_agents.runtime import run_workspace_agent
from blackbox.workspaces import WorkspaceSpec
from tests.fixtures.fake_openai_client import FakeOpenAIClient, evt, final_response
from tests.fixtures.scripted_model import text_only_turn, tool_call_turn
from tests.runtime.test_package_permissions import setup, spec
from tests.runtime.test_workspace_tool_loop import _WorkspaceWriteApprovalPolicy


@pytest.mark.parametrize("selection", ["static", "dynamic", "routed"])
async def test_custom_workspace_prefix_keeps_operation_grant(
    tmp_path: Path, selection: str
) -> None:
    runtime, scripted, _ = setup()
    (tmp_path / "notes.txt").write_text("approved read")
    package = replace(spec(), tools=[], permissions=[ToolPermission("workspace:read_file")])
    scripted.queue(
        tool_call_turn(
            call_id="read", name="my_workspace_read_file", arguments={"path": "notes.txt"}
        )
    )
    scripted.queue(text_only_turn("done"))
    kwargs: dict[str, Any] = {"tool_selection": selection}
    if selection == "routed":
        kwargs = {"tools": "dynamic", "tool_routing": ToolRoutingSpec(mode="auto")}
    await run_workspace_agent(
        runtime,
        package,
        input="read notes",
        workspace=WorkspaceSpec.local(tmp_path),
        workspace_prefix="my_workspace",
        **kwargs,
    )
    assert "my_workspace_read_file" in {tool["name"] for tool in scripted.calls[0].tools}
    exposed = next(
        tool for tool in scripted.calls[0].tools if tool["name"] == "my_workspace_read_file"
    )
    assert exposed["metadata"]["ref"] == "workspace:read_file"
    assert scripted.calls[1].input[0].status == "completed"
    assert scripted.calls[1].input[0].data["content"] == "approved read"


@pytest.mark.parametrize("approve_workspace", [True, False])
async def test_package_and_workspace_approvals_compose(
    tmp_path: Path, approve_workspace: bool
) -> None:
    runtime, scripted, _ = setup()
    package = replace(
        spec(),
        tools=[],
        permissions=[
            ToolPermission(
                "workspace:write_file", scopes=["write"], approval=ApprovalRequirement("always")
            )
        ],
    )
    scripted.queue(
        tool_call_turn(
            call_id="write",
            name="workspace_write_file",
            arguments={"path": "notes.txt", "content": "approved write"},
        )
    )
    scripted.queue(text_only_turn("done"))
    task = asyncio.create_task(
        run_workspace_agent(
            runtime,
            package,
            input="write notes",
            workspace=WorkspaceSpec.local(tmp_path),
            workspace_policy=_WorkspaceWriteApprovalPolicy(),
        )
    )
    try:
        for approved in [True, approve_workspace]:
            approval_id: str | None = None
            for _ in range(1000):
                pending = [
                    key
                    for loop in runtime._active_loops.values()
                    for key, future in loop._approvals.items()
                    if not future.done()
                ]
                if pending:
                    approval_id = pending[0]
                    break
                if task.done():
                    break
                await asyncio.sleep(0.001)
            assert approval_id is not None
            await runtime.approve(approval_id, ApprovalDecision(approved=approved))
        await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert (tmp_path / "notes.txt").exists() == approve_workspace
    assert scripted.calls[1].input[0].status == ("completed" if approve_workspace else "failed")
    if approve_workspace:
        assert (tmp_path / "notes.txt").read_text() == "approved write"


@pytest.mark.parametrize("source", ["caller", "config"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_native_tool_search_control_cannot_bypass_package(source: str, enabled: bool) -> None:
    runtime = AgentRuntime()
    client = FakeOpenAIClient()
    runtime.registry.register_model(OpenAIResponsesProvider(client=client, max_retries=0))
    client.queue(
        [evt("response.output_text.delta", delta="done", item_id="message")],
        final_response=final_response(id_="response"),
    )
    package = WorkspaceAgentSpec(
        "restricted", model_provider="openai", model="gpt-5.5", permission_mode="allowlist_v1"
    )
    control = ToolSearchControl(enabled=enabled)
    kwargs: dict[str, Any] = (
        {"tool_search": control}
        if source == "caller"
        else {"config": RuntimeConfig(overrides={"tool_search": control})}
    )
    if enabled:
        with pytest.raises(UnsupportedFeatureError, match="ToolSearchControl"):
            await run_workspace_agent(runtime, package, input="go", **kwargs)
        assert client.responses.seen_kwargs == []
    else:
        result = await run_workspace_agent(runtime, package, input="go", **kwargs)
        assert result.text == "done"
        assert len(client.responses.seen_kwargs) == 1
        assert not any(
            tool.get("type") == "tool_search"
            for tool in client.responses.seen_kwargs[0].get("tools", [])
        )
