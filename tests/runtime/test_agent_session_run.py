from __future__ import annotations

from dataclasses import dataclass

from agent_runtime import AgentRuntime, AgentSessionResult, EventTypes
from agent_runtime.agents.claude_code import ClaudeCodeAgentProvider
from agent_runtime.workspaces import WorkspaceSpec
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
        EventTypes.CLOUD_AGENT_STATUS_CHANGED,
        EventTypes.CLOUD_AGENT_LOG,
        EventTypes.SESSION_COMPLETED,
    ]
    assert [artifact.id for artifact in result.artifacts] == ["art_patch", "art_log"]
    assert result.session_ref.provider == "claude-code"
    assert result.session_ref.id == "sess_runtime_1"
    assert result.provider_state is not None
    assert result.provider_state.continuation["last_event_id"] == "evt_3"
    assert result.usage is not None
    assert result.usage.total_tokens == 15
    assert result.trace["spans"]
    assert client.started_tasks[0].workspace == WorkspaceSpec.local(".")

    run_id = result.events[0].run_id
    assert run_id is not None
    stored_events = await runtime.event_store.list_events(run_id)
    assert [event.id for event in stored_events] == ["evt_1", "evt_2", "evt_3"]

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
