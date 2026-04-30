from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from blackbox import AgentRuntime, EventTypes
from blackbox.core.approvals import ApprovalDecision
from blackbox.core.errors import ApprovalError
from blackbox.core.stores import SQLiteSessionStore
from blackbox.providers.agent_adapters.claude_code import ClaudeCodeAgentProvider
from tests.fixtures.fake_claude_code_client import FakeClaudeCodeClient


class RecordingClaudeClient(FakeClaudeCodeClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stream_calls: list[tuple[str, str | None]] = []

    async def stream_events(
        self,
        provider_session_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        self.stream_calls.append((provider_session_id, after_event_id))
        async for event in super().stream_events(
            provider_session_id,
            after_event_id=after_event_id,
        ):
            yield event


async def test_agent_session_state_and_events_persist_to_session_store(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    client = RecordingClaudeClient(
        events=[
            {"id": "evt_1", "type": "log", "message": "working"},
            {"id": "evt_2", "type": "completed", "message": "done"},
        ],
    )
    runtime = AgentRuntime(session_store=store)
    runtime.registry.register_agent(ClaudeCodeAgentProvider(client=client))

    session = await runtime.agents.create_session(
        provider="claude-code",
        agent="repo-fixer",
        task="Fix tests",
    )
    events = [event async for event in runtime.agents.stream(session)]

    assert [event.id for event in events] == ["evt_1", "evt_2"]
    state = await store.load_session_state(session.id)
    assert state is not None
    assert state.status == "completed"
    assert state.session_ref.metadata["provider_session_id"] == "provider_sess_1"
    assert state.provider_state is not None
    assert state.provider_state.continuation["provider_session_id"] == "provider_sess_1"
    assert state.last_event_id == "evt_2"
    assert state.run_ids == [events[0].run_id]

    replayed = await runtime.agents.replay(session, after_event_id="evt_1")
    assert [event.id for event in replayed] == ["evt_2"]
    await store.close()


async def test_terminal_session_replays_after_restart_without_provider_call(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions.sqlite"
    first_store = SQLiteSessionStore(path)
    first_client = RecordingClaudeClient(
        events=[
            {"id": "evt_1", "type": "log", "message": "working"},
            {"id": "evt_2", "type": "completed", "message": "done"},
        ],
    )
    first = AgentRuntime(session_store=first_store)
    first.registry.register_agent(ClaudeCodeAgentProvider(client=first_client))
    session = await first.agents.create_session(
        provider="claude-code",
        agent="repo-fixer",
        task="Fix tests",
    )
    _ = [event async for event in first.agents.stream(session)]
    await first_store.close()

    second_store = SQLiteSessionStore(path)
    second_client = RecordingClaudeClient(events=[])
    second = AgentRuntime(session_store=second_store)
    second.registry.register_agent(ClaudeCodeAgentProvider(client=second_client))

    restored = await second.agents.load_session(session.id)
    replayed = [event async for event in second.agents.stream(restored, after_event_id="evt_1")]

    assert [event.id for event in replayed] == ["evt_2"]
    assert second_client.stream_calls == []
    await second_store.close()


async def test_nonterminal_session_resumes_with_provider_cursor_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sessions.sqlite"
    first_store = SQLiteSessionStore(path)
    first_client = RecordingClaudeClient(
        events=[{"id": "evt_1", "type": "log", "message": "working"}],
    )
    first = AgentRuntime(session_store=first_store)
    first.registry.register_agent(ClaudeCodeAgentProvider(client=first_client))
    session = await first.agents.create_session(
        provider="claude-code",
        agent="repo-fixer",
        task="Fix tests",
    )
    first_events = [event async for event in first.agents.stream(session)]
    assert [event.id for event in first_events] == ["evt_1"]
    await first_store.close()

    second_store = SQLiteSessionStore(path)
    second_client = RecordingClaudeClient(
        events=[
            {"id": "evt_1", "type": "log", "message": "working"},
            {"id": "evt_2", "type": "completed", "message": "done"},
        ],
    )
    second = AgentRuntime(session_store=second_store)
    second.registry.register_agent(ClaudeCodeAgentProvider(client=second_client))

    restored = await second.agents.load_session(session.id)
    resumed = [event async for event in second.agents.stream(restored, after_event_id="evt_1")]

    assert [event.id for event in resumed] == ["evt_2"]
    assert second_client.stream_calls == [("provider_sess_1", "evt_1")]
    state = await second_store.load_session_state(session.id)
    assert state is not None
    assert state.status == "completed"
    assert resumed[-1].type == EventTypes.SESSION_COMPLETED
    await second_store.close()


async def test_approval_decision_is_idempotent_from_session_store(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    client = RecordingClaudeClient(
        events=[
            {
                "id": "evt_approval",
                "type": "approval_required",
                "approval_id": "approval_delete",
                "action": "delete",
            }
        ],
    )
    runtime = AgentRuntime(session_store=store)
    runtime.registry.register_agent(ClaudeCodeAgentProvider(client=client))
    session = await runtime.agents.create_session(
        provider="claude-code",
        agent="repo-fixer",
        task="Delete generated files",
    )
    _ = [event async for event in runtime.agents.stream(session)]

    await runtime.agents.approve(
        provider="claude-code",
        approval_id="approval_delete",
        decision=ApprovalDecision.approve("ok"),
    )
    await runtime.agents.approve(
        provider="claude-code",
        approval_id="approval_delete",
        decision=ApprovalDecision.approve("ok"),
    )

    assert len(client.approvals) == 1
    state = await store.load_session_state(session.id)
    assert state is not None
    assert state.pending_approvals["approval_delete"].status == "approved"

    with pytest.raises(ApprovalError):
        await runtime.agents.approve(
            provider="claude-code",
            approval_id="approval_delete",
            decision=ApprovalDecision.deny("blocked"),
        )
    await store.close()


async def test_send_message_idempotency_key_returns_original_invocation(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.sqlite")
    client = RecordingClaudeClient(
        events=[{"id": "evt_done", "type": "completed", "message": "done"}],
    )
    runtime = AgentRuntime(session_store=store)
    runtime.registry.register_agent(ClaudeCodeAgentProvider(client=client))
    session = await runtime.agents.create_session(
        provider="claude-code",
        agent="repo-fixer",
        task="Fix tests",
    )
    _ = [event async for event in runtime.agents.stream(session)]

    first = await runtime.agents.send_message(
        session,
        "Run tests again",
        idempotency_key="retry-1",
    )
    second = await runtime.agents.send_message(
        session,
        "Run tests again",
        idempotency_key="retry-1",
    )

    assert first.id == second.id == "inv_followup"
    assert client.messages == [("provider_sess_1", "Run tests again")]
    state = await store.load_session_state(session.id)
    assert state is not None
    assert state.idempotency_keys["retry-1"] == "inv_followup"
    await store.close()
