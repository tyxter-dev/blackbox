from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass(slots=True, frozen=True)
class AgentEvent:
    """A normalized runtime event.

    Events are the main public stream type. Provider-native payloads are preserved in
    ``raw`` and provider-specific metadata can be stored in ``data``.

    Correlation fields:
    - ``run_id`` is minted once per :meth:`AgentRuntime.run` / :meth:`AgentRuntime.stream`
      invocation and stamped on every event so a stream can be replayed or audited.
    - ``sequence`` is a monotonic integer assigned per-run, useful for ordering
      events that share a timestamp.
    - ``trace_id`` / ``span_id`` / ``parent_span_id`` form the workflow-level trace
      context. By default the runtime uses the run id as the trace id and creates
      child spans for model, tool, MCP, workspace, approval, artifact, handoff,
      guardrail, retry, and eval work.
    - ``provider_*`` fields preserve provider-native trace/request correlation
      without making those identifiers part of the runtime's stable trace model.
    """

    type: str
    run_id: str | None = None
    sequence: int | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    span_kind: str | None = None
    session_id: str | None = None
    provider: str | None = None
    item_id: str | None = None
    provider_trace_id: str | None = None
    provider_span_id: str | None = None
    provider_request_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    raw: Any | None = None
    id: str = field(default_factory=lambda: f"evt_{uuid4().hex}")
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class EventTypes:
    """Canonical event names used by the runtime.

    Providers may emit additional names, but should map common lifecycle steps to
    these constants where possible.
    """

    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"

    SESSION_CREATED = "session.created"
    SESSION_STARTED = "session.started"
    SESSION_COMPLETED = "session.completed"
    SESSION_FAILED = "session.failed"
    SESSION_CANCELLED = "session.cancelled"

    MODEL_REQUEST_STARTED = "model.request.started"
    MODEL_ITEM_CREATED = "model.item.created"
    MODEL_ITEM_COMPLETED = "model.item.completed"
    MODEL_TEXT_DELTA = "model.text.delta"
    MODEL_REASONING_DELTA = "model.reasoning.delta"
    MODEL_COMPLETED = "model.completed"

    TOOL_CALL_REQUESTED = "tool.call.requested"
    TOOL_CALL_STARTED = "tool.call.started"
    TOOL_CALL_COMPLETED = "tool.call.completed"
    TOOL_CALL_FAILED = "tool.call.failed"

    AGENT_TOOL_CALL_STARTED = "agent_tool.call.started"
    AGENT_TOOL_CALL_COMPLETED = "agent_tool.call.completed"
    AGENT_TOOL_CALL_FAILED = "agent_tool.call.failed"

    TOOL_SEARCH_REQUESTED = "tool_search.requested"
    TOOL_SEARCH_COMPLETED = "tool_search.completed"

    HOSTED_TOOL_CALL_REQUESTED = "hosted_tool.call.requested"
    HOSTED_TOOL_CALL_STARTED = "hosted_tool.call.started"
    HOSTED_TOOL_CALL_COMPLETED = "hosted_tool.call.completed"
    HOSTED_TOOL_CALL_FAILED = "hosted_tool.call.failed"
    HOSTED_TOOL_CALL_DENIED = "hosted_tool.call.denied"
    HOSTED_TOOL_OUTPUT_PREPARED = "hosted_tool.output.prepared"

    MCP_LIST_TOOLS_COMPLETED = "mcp.list_tools.completed"
    MCP_APPROVAL_REQUIRED = "mcp.approval.required"
    MCP_CALL_STARTED = "mcp.call.started"
    MCP_CALL_COMPLETED = "mcp.call.completed"

    CLOUD_AGENT_STATUS_CHANGED = "cloud_agent.status.changed"
    CLOUD_AGENT_LOG = "cloud_agent.log"
    CLOUD_AGENT_CHECKPOINT_CREATED = "cloud_agent.checkpoint.created"

    WORKSPACE_FILE_READ = "workspace.file.read"
    WORKSPACE_FILE_CHANGED = "workspace.file.changed"
    WORKSPACE_COMMAND_STARTED = "workspace.command.started"
    WORKSPACE_COMMAND_COMPLETED = "workspace.command.completed"
    WORKSPACE_PATCH_CREATED = "workspace.patch.created"

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_DENIED = "approval.denied"

    HANDOFF_REQUESTED = "handoff.requested"
    HANDOFF_STARTED = "handoff.started"
    HANDOFF_COMPLETED = "handoff.completed"
    HANDOFF_FAILED = "handoff.failed"

    GUARDRAIL_STARTED = "guardrail.started"
    GUARDRAIL_COMPLETED = "guardrail.completed"
    GUARDRAIL_FAILED = "guardrail.failed"

    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_UPDATED = "artifact.updated"

    RETRY_STARTED = "retry.started"
    RETRY_COMPLETED = "retry.completed"
    RETRY_FAILED = "retry.failed"

    EVAL_STARTED = "eval.started"
    EVAL_COMPLETED = "eval.completed"
    EVAL_FAILED = "eval.failed"
