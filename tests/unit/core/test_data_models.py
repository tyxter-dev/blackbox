from __future__ import annotations

from blackbox.core.approvals import ApprovalDecision, ApprovalRequest
from blackbox.core.artifacts import Artifact, ArtifactRef
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.items import ItemTypes, RunItem
from blackbox.core.sessions import AgentRef, AgentSession, SessionRef


def test_agent_event_defaults_and_unique_ids() -> None:
    a = AgentEvent(type=EventTypes.MODEL_TEXT_DELTA)
    b = AgentEvent(type=EventTypes.MODEL_TEXT_DELTA)
    assert a.id != b.id
    assert a.id.startswith("evt_")
    assert a.timestamp <= b.timestamp
    assert a.data == {}
    assert a.raw is None


def test_event_types_cover_prd_taxonomy() -> None:
    required = {
        "RUN_STARTED", "RUN_COMPLETED",
        "SESSION_CREATED", "SESSION_STARTED", "SESSION_COMPLETED",
        "SESSION_FAILED", "SESSION_CANCELLED",
        "MODEL_REQUEST_STARTED", "MODEL_TEXT_DELTA", "MODEL_REASONING_DELTA",
        "MODEL_COMPLETED", "MODEL_ITEM_CREATED", "MODEL_ITEM_COMPLETED",
        "TOOL_CALL_REQUESTED", "TOOL_CALL_STARTED", "TOOL_CALL_COMPLETED",
        "TOOL_CALL_FAILED",
        "TOOL_SEARCH_REQUESTED", "TOOL_SEARCH_COMPLETED",
        "MCP_LIST_TOOLS_COMPLETED", "MCP_APPROVAL_REQUIRED",
        "MCP_CALL_STARTED", "MCP_CALL_COMPLETED",
        "WORKSPACE_FILE_CHANGED", "WORKSPACE_COMMAND_STARTED",
        "WORKSPACE_PATCH_CREATED",
        "APPROVAL_REQUESTED", "APPROVAL_APPROVED", "APPROVAL_DENIED",
        "ARTIFACT_CREATED", "ARTIFACT_UPDATED",
    }
    for name in required:
        assert hasattr(EventTypes, name), f"missing canonical event: {name}"


def test_run_item_defaults_and_id_prefix() -> None:
    item = RunItem(type=ItemTypes.MESSAGE, provider="x")
    assert item.id.startswith("item_")
    assert item.status is None
    assert item.parent_id is None


def test_artifact_ref_round_trip() -> None:
    art = Artifact(type="patch", name="fix.diff", uri="file:///tmp/fix.diff")
    ref = art.ref
    assert isinstance(ref, ArtifactRef)
    assert ref.id == art.id
    assert ref.uri == art.uri


def test_approval_decision_helpers() -> None:
    approve = ApprovalDecision.approve("looks safe")
    deny = ApprovalDecision.deny("policy block")
    assert approve.approved is True and approve.reason == "looks safe"
    assert deny.approved is False and deny.reason == "policy block"


def test_approval_request_has_unique_id() -> None:
    a = ApprovalRequest(action="run_command")
    b = ApprovalRequest(action="run_command")
    assert a.id != b.id
    assert a.id.startswith("approval_")


def test_session_ref_is_derived_from_session() -> None:
    session = AgentSession(provider="local", task="t", agent_id="a")
    ref = session.ref
    assert isinstance(ref, SessionRef)
    assert ref.id == session.id
    assert ref.provider == "local"
    assert ref.agent_id == "a"


def test_agent_ref_is_hashable_dataclass() -> None:
    ref = AgentRef(provider="local", id="alpha")
    assert ref.provider == "local" and ref.id == "alpha"
