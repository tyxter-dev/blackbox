from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_runtime import AgentRuntime, AgentSessionResult, EventTypes
from agent_runtime.agents.claude_code import ClaudeCodeAgentProvider
from agent_runtime.workspaces import WorkspaceRef, WorkspaceSpec
from tests.fixtures.fake_claude_code_client import FakeClaudeCodeClient


@dataclass(slots=True)
class PatchSummary:
    summary: str
    tests: list[str]


async def test_agents_run_collects_session_result_with_output_and_artifacts() -> None:
    client = FakeClaudeCodeClient(
        events=[
            {"id": "evt_1", "type": "status", "status": "running"},
            {"id": "evt_2", "type": "log", "message": "working"},
            {
                "id": "evt_3",
                "type": "completed",
                "message": '{"summary": "fixed tests", "tests": ["pytest"]}',
                "data": {
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                    }
                },
            },
        ],
        artifacts=[
            {"id": "art_patch", "type": "patch", "name": "fix.patch", "data": "diff"},
            {"id": "art_log", "type": "log", "name": "pytest.log", "data": "ok"},
        ],
    )
    runtime = AgentRuntime()
    runtime.registry.register_agent(ClaudeCodeAgentProvider(client=client))

    result: AgentSessionResult[PatchSummary] = await runtime.agents.run(
        provider="claude-code",
        agent="repo-fixer",
        task="Fix failing tests",
        workspace=WorkspaceSpec.local("."),
        output_type=PatchSummary,
    )

    assert result.output == PatchSummary(summary="fixed tests", tests=["pytest"])
    assert result.text == '{"summary": "fixed tests", "tests": ["pytest"]}'
    assert result.status == "completed"
    assert [event.type for event in result.events] == [
        EventTypes.WORKSPACE_OPENED,
        EventTypes.CLOUD_AGENT_STATUS_CHANGED,
        EventTypes.CLOUD_AGENT_LOG,
        EventTypes.SESSION_COMPLETED,
        EventTypes.WORKSPACE_CLOSED,
    ]
    assert [artifact.id for artifact in result.artifacts] == ["art_patch", "art_log"]
    assert result.session_ref.provider == "claude-code"
    assert result.session_ref.id == "sess_runtime_1"
    assert result.provider_state is not None
    assert result.provider_state.continuation["last_event_id"] == "evt_3"
    assert result.usage is not None
    assert result.usage.total_tokens == 15
    assert result.trace["spans"]
    assert isinstance(client.started_tasks[0].workspace, WorkspaceRef)
    assert client.started_tasks[0].workspace.kind == "local"

    run_id = result.events[0].run_id
    assert run_id is not None
    stored_events = await runtime.event_store.list_events(run_id)
    assert [event.id for event in stored_events if event.provider == "claude-code"] == [
        "evt_1",
        "evt_2",
        "evt_3",
    ]
    assert [event.type for event in stored_events] == [event.type for event in result.events]

    stored_state = await runtime.run_store.load(result.session_ref.id)
    assert stored_state is not None
    assert stored_state.provider_state is not None
    assert stored_state.provider_state.conversation_id == result.session_ref.id


async def test_agents_run_can_filter_collected_artifacts() -> None:
    client = FakeClaudeCodeClient(
        events=[{"id": "evt_done", "type": "completed", "message": "done"}],
        artifacts=[
            {"id": "art_patch", "type": "patch", "name": "fix.patch", "data": "diff"},
            {"id": "art_log", "type": "log", "name": "pytest.log", "data": "ok"},
        ],
    )
    runtime = AgentRuntime()
    runtime.registry.register_agent(ClaudeCodeAgentProvider(client=client))

    result: AgentSessionResult[str] = await runtime.agents.run(
        provider="claude-code",
        agent="repo-fixer",
        task="Fix failing tests",
        artifact_type="patch",
    )

    assert result.output == "done"
    assert result.status == "completed"
    assert [artifact.id for artifact in result.artifacts] == ["art_patch"]


async def test_agents_run_exposes_waiting_for_approval_status() -> None:
    client = FakeClaudeCodeClient(
        events=[
            {"id": "evt_1", "type": "status", "status": "running"},
            {
                "id": "evt_2",
                "type": "approval_required",
                "approval_id": "approval_delete",
            },
        ],
    )
    runtime = AgentRuntime()
    runtime.registry.register_agent(ClaudeCodeAgentProvider(client=client))

    result: AgentSessionResult[str] = await runtime.agents.run(
        provider="claude-code",
        agent="repo-fixer",
        task="Delete generated files",
    )

    assert result.status == "waiting_for_approval"
    assert result.metadata["session"]["status"] == "waiting_for_approval"
    assert result.metadata["session"]["lifecycle_status"] == "waiting"


async def test_agents_run_routes_workspace_ref_through_workspace_provider(
    tmp_path: Path,
) -> None:
    if shutil.which("git") is None:
        import pytest

        pytest.skip("git is required for the git workspace provider test.")
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("demo\n")
    _git(source, "init")
    _git(source, "checkout", "-b", "main")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Agent Runtime")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "initial")

    client = FakeClaudeCodeClient(
        events=[{"id": "evt_done", "type": "completed", "message": "done"}],
    )
    runtime = AgentRuntime()
    runtime.registry.register_agent(ClaudeCodeAgentProvider(client=client))

    workspace = await runtime.workspaces.open(
        WorkspaceSpec.git(url=str(source), ref="main")
    )
    result: AgentSessionResult[str] = await runtime.agents.run(
        provider="claude-code",
        agent="coding-agent",
        task="Implement the feature and return a patch",
        workspace=workspace,
    )

    started_workspace = client.started_tasks[0].workspace
    assert isinstance(started_workspace, WorkspaceRef)
    assert started_workspace.id == workspace.id
    assert started_workspace.kind == "git"
    assert result.status == "completed"
    assert EventTypes.WORKSPACE_OPENED in [event.type for event in result.events]
    assert result.metadata["workspaces"][workspace.id]["provider"] == "git-workspace"


async def test_provider_native_workspace_events_gain_workspace_context(
    tmp_path: Path,
) -> None:
    client = FakeClaudeCodeClient(
        events=[
            {"id": "evt_file", "type": "file_changed", "path": "src/app.py"},
            {"id": "evt_test", "type": "test_completed", "status": "passed"},
            {"id": "evt_done", "type": "completed", "message": "done"},
        ],
    )
    runtime = AgentRuntime()
    runtime.registry.register_agent(ClaudeCodeAgentProvider(client=client))

    result: AgentSessionResult[str] = await runtime.agents.run(
        provider="claude-code",
        agent="coding-agent",
        task="Run tests",
        workspace=WorkspaceSpec.local(tmp_path),
    )

    workspace = client.started_tasks[0].workspace
    assert isinstance(workspace, WorkspaceRef)
    assert [event.type for event in result.events] == [
        EventTypes.WORKSPACE_OPENED,
        EventTypes.WORKSPACE_FILE_CHANGED,
        EventTypes.WORKSPACE_TEST_COMPLETED,
        EventTypes.SESSION_COMPLETED,
        EventTypes.WORKSPACE_CLOSED,
    ]
    file_event = result.events[1]
    assert file_event.data["workspace_id"] == workspace.id
    assert file_event.data["workspace_kind"] == "local"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
