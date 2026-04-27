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

    REALTIME_SESSION_CONNECTING = "realtime.session.connecting"
    REALTIME_SESSION_CONNECTED = "realtime.session.connected"
    REALTIME_SESSION_UPDATED = "realtime.session.updated"
    REALTIME_SESSION_CLOSED = "realtime.session.closed"
    REALTIME_SESSION_FAILED = "realtime.session.failed"
    REALTIME_TRANSPORT_RECONNECTING = "realtime.transport.reconnecting"
    REALTIME_TRANSPORT_RECONNECTED = "realtime.transport.reconnected"

    REALTIME_INPUT_ITEM_CREATED = "realtime.input.item.created"
    REALTIME_INPUT_TEXT_SUBMITTED = "realtime.input.text.submitted"
    REALTIME_INPUT_AUDIO_DELTA = "realtime.input.audio.delta"
    REALTIME_INPUT_AUDIO_COMMITTED = "realtime.input.audio.committed"
    REALTIME_INPUT_SPEECH_STARTED = "realtime.input.speech.started"
    REALTIME_INPUT_SPEECH_STOPPED = "realtime.input.speech.stopped"
    REALTIME_INPUT_TRANSCRIPT_DELTA = "realtime.input.transcript.delta"
    REALTIME_INPUT_TRANSCRIPT_COMPLETED = "realtime.input.transcript.completed"
    REALTIME_INPUT_IMAGE_ADDED = "realtime.input.image.added"
    REALTIME_INPUT_VIDEO_FRAME_ADDED = "realtime.input.video_frame.added"

    REALTIME_RESPONSE_CREATED = "realtime.response.created"
    REALTIME_RESPONSE_COMPLETED = "realtime.response.completed"
    REALTIME_RESPONSE_CANCELLED = "realtime.response.cancelled"
    REALTIME_RESPONSE_FAILED = "realtime.response.failed"
    REALTIME_OUTPUT_ITEM_CREATED = "realtime.output.item.created"
    REALTIME_OUTPUT_ITEM_COMPLETED = "realtime.output.item.completed"
    REALTIME_OUTPUT_TEXT_DELTA = "realtime.output.text.delta"
    REALTIME_OUTPUT_TEXT_DONE = "realtime.output.text.done"
    REALTIME_OUTPUT_AUDIO_DELTA = "realtime.output.audio.delta"
    REALTIME_OUTPUT_AUDIO_DONE = "realtime.output.audio.done"
    REALTIME_OUTPUT_AUDIO_TRANSCRIPT_DELTA = "realtime.output.audio_transcript.delta"
    REALTIME_OUTPUT_AUDIO_TRANSCRIPT_DONE = "realtime.output.audio_transcript.done"

    REALTIME_TURN_STARTED = "realtime.turn.started"
    REALTIME_TURN_COMPLETED = "realtime.turn.completed"
    REALTIME_INTERRUPTION_DETECTED = "realtime.interruption.detected"
    REALTIME_OUTPUT_TRUNCATED = "realtime.output.truncated"
    REALTIME_HISTORY_UPDATED = "realtime.history.updated"
    REALTIME_USAGE_REPORTED = "realtime.usage.reported"
    REALTIME_TOOL_ARGUMENTS_DELTA = "realtime.tool.arguments.delta"

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

    MCP_SERVER_STARTING = "mcp.server.starting"
    MCP_SERVER_STARTED = "mcp.server.started"
    MCP_SERVER_STOPPING = "mcp.server.stopping"
    MCP_SERVER_STOPPED = "mcp.server.stopped"
    MCP_SERVER_FAILED = "mcp.server.failed"
    MCP_SERVER_STDERR = "mcp.server.stderr"
    MCP_AUTH_CHALLENGE = "mcp.auth.challenge"
    MCP_LIST_TOOLS_STARTED = "mcp.list_tools.started"
    MCP_LIST_TOOLS_COMPLETED = "mcp.list_tools.completed"
    MCP_LIST_TOOLS_FAILED = "mcp.list_tools.failed"
    MCP_TOOLS_CACHE_HIT = "mcp.tools.cache.hit"
    MCP_TOOLS_CACHE_MISS = "mcp.tools.cache.miss"
    MCP_TOOLS_CACHE_INVALIDATED = "mcp.tools.cache.invalidated"
    MCP_APPROVAL_REQUIRED = "mcp.approval.required"
    MCP_CALL_STARTED = "mcp.call.started"
    MCP_CALL_COMPLETED = "mcp.call.completed"
    MCP_CALL_FAILED = "mcp.call.failed"

    CLOUD_AGENT_STATUS_CHANGED = "cloud_agent.status.changed"
    CLOUD_AGENT_LOG = "cloud_agent.log"
    CLOUD_AGENT_CHECKPOINT_CREATED = "cloud_agent.checkpoint.created"

    WORKSPACE_FILE_READ = "workspace.file.read"
    WORKSPACE_FILE_CHANGED = "workspace.file.changed"
    WORKSPACE_OPENED = "workspace.opened"
    WORKSPACE_CLOSED = "workspace.closed"
    WORKSPACE_FILE_LISTED = "workspace.file.listed"
    WORKSPACE_COMMAND_STARTED = "workspace.command.started"
    WORKSPACE_COMMAND_OUTPUT = "workspace.command.output"
    WORKSPACE_COMMAND_COMPLETED = "workspace.command.completed"
    WORKSPACE_TEST_STARTED = "workspace.test.started"
    WORKSPACE_TEST_COMPLETED = "workspace.test.completed"
    WORKSPACE_PATCH_CREATED = "workspace.patch.created"
    WORKSPACE_SNAPSHOT_CREATED = "workspace.snapshot.created"
    WORKSPACE_SNAPSHOT_RESTORED = "workspace.snapshot.restored"
    WORKSPACE_PORT_EXPOSED = "workspace.port.exposed"
    WORKSPACE_PORT_CLOSED = "workspace.port.closed"
    WORKSPACE_ARTIFACT_EXPORTED = "workspace.artifact.exported"

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
