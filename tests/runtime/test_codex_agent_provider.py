from __future__ import annotations

import asyncio
import os
import textwrap
from pathlib import Path
from typing import Any

import pytest

from blackbox.core.approvals import ApprovalDecision
from blackbox.core.errors import UnsupportedFeatureError
from blackbox.core.events import EventTypes
from blackbox.providers.agent_adapters.codex import (
    CodexAgentProvider,
    _process_environment,
    _thread_start_params,
    _turn_start_params,
)
from blackbox.providers.base import AgentSpec, TaskSpec
from blackbox.workspaces import WorkspaceSpec
from tests.fixtures.fake_codex_client import FakeCodexAppServerClient


def _event(event_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"id": event_id, "method": method, "params": params}


async def test_codex_client_backed_lifecycle_streams_and_replays() -> None:
    client = FakeCodexAppServerClient(
        events=[
            _event("evt_1", "turn/started", {"threadId": "thread_1", "turn": {"id": "turn_1"}}),
            _event(
                "evt_2",
                "item/agentMessage/delta",
                {"delta": "working", "itemId": "item_message", "threadId": "thread_1", "turnId": "turn_1"},
            ),
            _event(
                "evt_3",
                "item/started",
                {
                    "item": {"id": "item_command", "type": "commandExecution"},
                    "threadId": "thread_1",
                    "turnId": "turn_1",
                },
            ),
            _event(
                "evt_4",
                "item/completed",
                {
                    "item": {"id": "item_change", "type": "fileChange"},
                    "threadId": "thread_1",
                    "turnId": "turn_1",
                },
            ),
            _event(
                "evt_5",
                "blackbox/approval/requested",
                {"action": "command", "approvalId": "codex_approval_item_command", "threadId": "thread_1"},
            ),
            _event(
                "evt_6",
                "turn/completed",
                {"threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}},
            ),
        ]
    )
    provider = CodexAgentProvider(client=client)

    agent = await provider.create_agent(AgentSpec(name="coder", instructions="Ship code."))
    session = await provider.start_session(agent, TaskSpec(prompt="fix bug", model="gpt-5.6-terra"))
    events = [event async for event in provider.stream_events(session)]

    assert provider.capabilities().supports_sessions is True
    assert provider.capabilities().supports_resume is False
    assert agent.id == "codex_agent_coder"
    assert session.id == "thread_1"
    assert [event.type for event in events] == [
        EventTypes.MODEL_REQUEST_STARTED,
        EventTypes.MODEL_TEXT_DELTA,
        EventTypes.WORKSPACE_COMMAND_STARTED,
        EventTypes.WORKSPACE_FILE_CHANGED,
        EventTypes.APPROVAL_REQUESTED,
        EventTypes.SESSION_COMPLETED,
    ]
    assert events[1].data["delta"] == "working"
    assert events[1].raw == client.events[1]
    assert session.status == "completed"

    replayed = [event async for event in provider.stream_events(session, after_event_id="evt_3")]
    assert [event.id for event in replayed] == ["evt_4", "evt_5", "evt_6"]


async def test_codex_client_backed_controls_and_artifacts() -> None:
    client = FakeCodexAppServerClient(
        artifacts=[
            {"id": "art_patch", "type": "file_change", "name": "app.py", "data": "diff"},
            {"id": "art_log", "type": "log", "name": "test.log", "data": "ok"},
        ]
    )
    provider = CodexAgentProvider(client=client)
    agent = await provider.create_agent(AgentSpec(name="coder"))
    session = await provider.start_session(agent, TaskSpec(prompt="fix bug"))

    invocation = await provider.send_message(session, "continue")
    await provider.approve("codex_approval_item_command", ApprovalDecision.approve("allowed"))
    await provider.cancel(session)
    artifacts = await provider.list_artifacts(session, type="file_change")

    assert invocation.id == "turn_followup"
    assert client.messages == [("thread_1", "continue")]
    assert client.approvals == [("codex_approval_item_command", ApprovalDecision.approve("allowed"))]
    assert client.cancelled == ["thread_1"]
    assert session.status == "cancelled"
    assert [artifact.id for artifact in artifacts.items] == ["art_patch"]


@pytest.mark.skipif(os.name == "nt", reason="The test app-server stub uses a POSIX shebang.")
async def test_codex_pinned_transport_pauses_and_resolves_native_approval(
    tmp_path: Path,
) -> None:
    app_server = tmp_path / "fake-codex"
    app_server.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import sys

            def emit(message):
                print(json.dumps(message), flush=True)

            for line in sys.stdin:
                message = json.loads(line)
                method = message.get("method")
                if method == "initialize":
                    emit({"id": message["id"], "result": {}})
                elif method == "thread/start":
                    emit({"id": message["id"], "result": {"thread": {"id": "thread_1"}}})
                elif method == "turn/start":
                    emit({"id": message["id"], "result": {"turn": {"id": "turn_1"}}})
                    emit({"method": "turn/started", "params": {"threadId": "thread_1", "turn": {"id": "turn_1"}}})
                    emit({"id": "approval_request_1", "method": "item/commandExecution/requestApproval", "params": {"itemId": "item_command", "startedAtMs": 1, "threadId": "thread_1", "turnId": "turn_1", "command": "true"}})
                elif message.get("id") == "approval_request_1":
                    assert message["result"] == {"decision": "accept"}
                    emit({"method": "turn/completed", "params": {"threadId": "thread_1", "turn": {"id": "turn_1", "status": "completed"}}})
            """
        )
    )
    app_server.chmod(0o755)
    provider = CodexAgentProvider(codex_bin=str(app_server))
    agent = await provider.create_agent(AgentSpec(name="coder"))
    session = await provider.start_session(agent, TaskSpec(prompt="run command"))

    stream = provider.stream_events(session).__aiter__()
    initial_events = [await asyncio.wait_for(anext(stream), timeout=1) for _ in range(2)]
    approval = next(event for event in initial_events if event.type == EventTypes.APPROVAL_REQUESTED)
    await provider.approve(approval.item_id or "", ApprovalDecision.approve())
    remaining = [event async for event in stream]
    await provider.close()

    assert approval.data["action"] == "command"
    assert remaining[-1].type == EventTypes.SESSION_COMPLETED


def test_codex_thread_and_turn_parameters_are_explicit_and_ephemeral() -> None:
    spec = AgentSpec(
        name="coder",
        instructions="Ship code.",
        model="agent-model",
        permissions={"sandbox": "workspace-write"},
    )
    task = TaskSpec(
        prompt="fix bug",
        model="task-model",
        workspace=WorkspaceSpec.local("/tmp/project"),
    )

    thread = _thread_start_params(spec, task)
    turn = _turn_start_params("thread_1", task.prompt, spec, task)

    assert thread == {
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
        "ephemeral": True,
        "model": "task-model",
        "developerInstructions": "Ship code.",
        "cwd": "/tmp/project",
        "sandbox": "workspace-write",
    }
    assert turn["input"] == [{"type": "text", "text": "fix bug"}]
    assert turn["model"] == "task-model"
    assert turn["sandboxPolicy"] == {"type": "workspaceWrite"}


@pytest.mark.parametrize("field", ["tools", "hosted_tools", "mcp_servers"])
async def test_codex_rejects_unmapped_agent_surfaces(field: str) -> None:
    provider = CodexAgentProvider(client=FakeCodexAppServerClient())
    kwargs: dict[str, Any] = {field: ["unsupported"]}

    with pytest.raises(UnsupportedFeatureError, match="does not map"):
        await provider.create_agent(AgentSpec(name="coder", **kwargs))


def test_codex_subscription_runtime_never_inherits_an_openai_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-api-billing-must-not-be-used")

    environment = _process_environment({}, None)

    assert "OPENAI_API_KEY" not in environment
    with pytest.raises(UnsupportedFeatureError, match="subscription-only"):
        _process_environment({"OPENAI_API_KEY": "sk-test"}, None)
