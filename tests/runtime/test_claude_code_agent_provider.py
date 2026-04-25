from __future__ import annotations

from agent_runtime.agents.claude_code import ClaudeCodeAgentProvider
from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.events import EventTypes
from agent_runtime.providers.base import AgentSpec, TaskSpec
from tests.fixtures.fake_claude_code_client import FakeClaudeCodeClient


async def test_claude_code_client_backed_lifecycle_streams_and_resumes() -> None:
    client = FakeClaudeCodeClient(
        events=[
            {"id": "evt_1", "type": "status", "status": "running"},
            {"id": "evt_2", "type": "log", "message": "working"},
            {"id": "evt_3", "type": "file_changed", "path": "app.py"},
            {"id": "evt_4", "type": "approval_required", "approval_id": "approval_1"},
            {"id": "evt_5", "type": "completed"},
        ],
    )
    provider = ClaudeCodeAgentProvider(client=client)

    agent = await provider.create_agent(AgentSpec(name="coder", instructions="Ship code."))
    session = await provider.start_session(agent, TaskSpec(prompt="fix bug", model="claude"))
    events = [event async for event in provider.stream_events(session)]

    assert provider.capabilities().supports_sessions is True
    assert agent.id == "agent_coder"
    assert session.id == "sess_runtime_1"
    assert session.metadata["provider_session_id"] == "provider_sess_1"
    assert [event.type for event in events] == [
        EventTypes.CLOUD_AGENT_STATUS_CHANGED,
        EventTypes.CLOUD_AGENT_LOG,
        EventTypes.WORKSPACE_FILE_CHANGED,
        EventTypes.APPROVAL_REQUESTED,
        EventTypes.SESSION_COMPLETED,
    ]
    assert session.status == "completed"

    resumed = [event async for event in provider.stream_events(session, after_event_id="evt_3")]
    assert [event.id for event in resumed] == ["evt_4", "evt_5"]


async def test_claude_code_client_backed_controls_and_artifacts() -> None:
    client = FakeClaudeCodeClient(
        artifacts=[
            {"id": "art_patch", "type": "patch", "name": "fix.patch", "data": "diff"},
            {"id": "art_log", "type": "log", "name": "pytest.log", "data": "ok"},
        ],
    )
    provider = ClaudeCodeAgentProvider(client=client)
    agent = await provider.create_agent(AgentSpec(name="coder"))
    session = await provider.start_session(agent, TaskSpec(prompt="fix bug"))

    invocation = await provider.send_message(session, "continue")
    await provider.approve("approval_1", ApprovalDecision.approve("ok"))
    await provider.cancel(session)
    artifacts = await provider.list_artifacts(session, type="patch")

    assert invocation.id == "inv_followup"
    assert client.messages == [("provider_sess_1", "continue")]
    assert client.approvals[0][0] == "approval_1"
    assert client.cancelled == ["provider_sess_1"]
    assert session.status == "cancelled"
    assert [artifact.id for artifact in artifacts.items] == ["art_patch"]

