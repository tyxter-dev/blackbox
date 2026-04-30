from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from blackbox import AgentRuntime, ApprovalDecision, EventTypes
from blackbox.core.errors import ConfigurationError
from blackbox.core.events import AgentEvent
from blackbox.core.policy import PolicyDecision, PolicyRequest
from blackbox.core.results import AgentResult
from blackbox.core.state import ProviderState
from blackbox.workspaces import (
    CommandResult,
    FakeSandboxClient,
    SandboxWorkspaceProvider,
    WorkspaceSpec,
)
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn, tool_call_turn


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


async def test_runtime_run_accepts_local_workspace_spec(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("ship it")
    runtime, scripted = _runtime()
    scripted.queue(
        tool_call_turn(
            call_id="ws_read",
            name="workspace_read_file",
            arguments={"path": "notes.txt"},
        )
    )
    scripted.queue(text_only_turn("done"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="read notes",
        workspace=WorkspaceSpec.local(tmp_path),
    )

    assert result.text == "done"
    assert [payload.tool_name for payload in result.payloads] == ["workspace_read_file"]
    event_types = [event.type for event in result.events]
    assert EventTypes.WORKSPACE_OPENED in event_types
    assert EventTypes.WORKSPACE_FILE_READ in event_types
    assert EventTypes.WORKSPACE_CLOSED in event_types
    assert "workspaces" in result.metadata
    assert "workspace_read_file" not in [tool.name for tool in runtime.tools.all_tools()]


async def test_runtime_run_accepts_sandbox_workspace_provider(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "test_app.py").write_text("def test_ok(): assert True")
    runtime, scripted = _runtime()
    workspace_provider = SandboxWorkspaceProvider(
        FakeSandboxClient(scripted_results=[CommandResult(exit_code=0, stdout="1 passed")])
    )

    def workspace_turn(request: Any) -> Any:
        from blackbox.core.events import AgentEvent

        tool_names = [tool["name"] for tool in request.tools]
        assert "workspace_run_command" in tool_names
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider="scripted")
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id="cmd",
            data={
                "call_id": "cmd",
                "name": "workspace_run_command",
                "arguments": {"command": "pytest -q", "cwd": "repo"},
            },
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted")},
        )

    scripted.queue(workspace_turn)
    scripted.queue(text_only_turn("fixed"))

    result: AgentResult[str] = await runtime.run(
        provider="scripted:test",
        input="run tests",
        workspace=WorkspaceSpec.sandbox(inputs={"repo": str(tmp_path / "repo")}),
        workspace_provider=workspace_provider,
    )

    assert result.text == "fixed"
    assert result.payloads[0].payload["result"].stdout == "1 passed"
    event_types = [event.type for event in result.events]
    assert EventTypes.WORKSPACE_OPENED in event_types
    assert EventTypes.WORKSPACE_COMMAND_OUTPUT in event_types
    assert EventTypes.WORKSPACE_COMMAND_COMPLETED in event_types


async def test_runtime_requires_provider_for_sandbox_workspace() -> None:
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("unreachable"))

    with pytest.raises(ConfigurationError, match="workspace_provider is required"):
        await runtime.run(
            provider="scripted:test",
            input="x",
            workspace=WorkspaceSpec.sandbox(),
        )


class _WorkspaceWriteApprovalPolicy:
    async def check(self, request: PolicyRequest) -> PolicyDecision:
        if request.checkpoint == "before_workspace_write":
            return PolicyDecision.require_approval("review write")
        return PolicyDecision.allow()


async def test_workspace_write_approval_resumes_runtime_stream(tmp_path: Path) -> None:
    runtime, scripted = _runtime()
    scripted.queue(
        tool_call_turn(
            call_id="write",
            name="workspace_write_file",
            arguments={"path": "notes.txt", "content": "approved"},
        )
    )
    scripted.queue(text_only_turn("done"))

    events: list[AgentEvent] = []

    async def collect() -> None:
        async for event in runtime.stream(
            provider="scripted:test",
            input="write notes",
            workspace=WorkspaceSpec.local(tmp_path),
            workspace_policy=_WorkspaceWriteApprovalPolicy(),
        ):
            events.append(event)

    task = asyncio.create_task(collect())

    approval_id: str | None = None
    for _ in range(100):
        for loop in runtime._active_loops.values():
            if loop._approvals:
                approval_id = next(iter(loop._approvals))
                break
        if approval_id is not None:
            break
        await asyncio.sleep(0.01)

    assert approval_id is not None
    await runtime.approve(approval_id, ApprovalDecision.approve("ok"))
    await task

    assert (tmp_path / "notes.txt").read_text() == "approved"
    event_types = [event.type for event in events]
    assert EventTypes.APPROVAL_REQUESTED in event_types
    assert EventTypes.APPROVAL_APPROVED in event_types
    assert EventTypes.WORKSPACE_FILE_CHANGED in event_types
    assert EventTypes.TOOL_CALL_COMPLETED in event_types
