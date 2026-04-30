from __future__ import annotations

import json

from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.serialization import (
    agent_session_state_from_dict,
    agent_session_state_to_dict,
)
from blackbox.core.session_state import (
    AgentSessionState,
    PendingApprovalState,
    SessionEventCursor,
    SessionInvocationState,
    apply_session_event,
)
from blackbox.core.sessions import AgentRef, AgentSession
from blackbox.core.state import ProviderState


def test_agent_session_state_round_trip_preserves_resume_metadata() -> None:
    session = AgentSession(
        provider="claude-code",
        agent_id="repo-maintainer",
        task="fix tests",
        model="claude-sonnet",
        status="running",
        metadata={
            "provider_session_id": "provider_sess_1",
            "runtime_session_id": "sess_runtime_1",
            "unknown": {"survives": True},
        },
        id="sess_runtime_1",
    )
    state = AgentSessionState(
        schema_version=1,
        session=session,
        session_ref=session.ref,
        provider=session.provider,
        status=session.status,
        agent_ref=AgentRef(provider="claude-code", id="repo-maintainer"),
        task="fix tests",
        model="claude-sonnet",
        provider_state=ProviderState(
            provider="claude-code",
            conversation_id="provider_sess_1",
            continuation={
                "provider_session_id": "provider_sess_1",
                "last_event_id": "evt_1",
            },
        ),
        invocations=[
            SessionInvocationState(
                id="inv_1",
                session_id=session.id,
                provider=session.provider,
                message="fix tests",
                idempotency_key="initial",
            )
        ],
        pending_approvals={
            "approval_1": PendingApprovalState(
                approval_id="approval_1",
                session_id=session.id,
                action="Bash",
                arguments={"cmd": "pytest"},
            )
        },
        workspace_ref={"id": "workspace_1", "kind": "git"},
        mcp_state={"servers": {"github": {"route": "provider_native"}}},
        metadata={"custom": {"value": 1}},
    )

    encoded = agent_session_state_to_dict(state)
    decoded = agent_session_state_from_dict(json.loads(json.dumps(encoded)))

    assert decoded.session.id == "sess_runtime_1"
    assert decoded.session.metadata["provider_session_id"] == "provider_sess_1"
    assert decoded.session_ref.metadata["unknown"] == {"survives": True}
    assert decoded.provider_state is not None
    assert decoded.provider_state.continuation["provider_session_id"] == "provider_sess_1"
    assert decoded.invocations[0].idempotency_key == "initial"
    assert decoded.pending_approvals["approval_1"].arguments == {"cmd": "pytest"}
    assert decoded.workspace_ref == {"id": "workspace_1", "kind": "git"}
    assert decoded.mcp_state["servers"]["github"]["route"] == "provider_native"
    assert decoded.metadata["custom"] == {"value": 1}


def test_apply_session_event_updates_status_cursors_and_approval_state() -> None:
    session = AgentSession(provider="local", task="go", id="sess_1")
    state = AgentSessionState(
        schema_version=1,
        session=session,
        session_ref=session.ref,
        provider="local",
        status="created",
    )
    event = AgentEvent(
        id="evt_approval",
        type=EventTypes.APPROVAL_REQUESTED,
        run_id="run_1",
        sequence=3,
        session_id="sess_1",
        provider="local",
        data={"approval_id": "approval_1", "action": "delete", "arguments": {"path": "x"}},
    )
    cursor = SessionEventCursor(
        session_id="sess_1",
        event_id="evt_approval",
        session_sequence=0,
        run_id="run_1",
        run_sequence=3,
        provider_event_id="provider_evt_1",
        provider_cursor="cursor_1",
    )

    apply_session_event(state, event, cursor)

    assert state.status == "waiting"
    assert state.session.status == "waiting"
    assert state.last_event_id == "evt_approval"
    assert state.last_provider_cursor == "cursor_1"
    assert state.run_ids == ["run_1"]
    assert state.pending_approvals["approval_1"].action == "delete"
