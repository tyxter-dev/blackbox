from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

ItemStatus = Literal["created", "in_progress", "completed", "failed", "requires_action"]


@dataclass(slots=True, frozen=True)
class RunItem:
    """A durable item produced or consumed by a run.

    Items are more persistent than events. An item can represent a message, tool call,
    tool result, hosted tool call, cloud-agent log entry, patch, artifact, or approval.
    """

    type: str
    provider: str
    data: dict[str, Any] = field(default_factory=dict)
    status: ItemStatus | None = None
    id: str = field(default_factory=lambda: f"item_{uuid4().hex}")
    parent_id: str | None = None
    raw: Any | None = None


class ItemTypes:
    """Canonical runtime item type constants used in durable run items."""

    MESSAGE = "message"
    REASONING = "reasoning"
    FUNCTION_CALL = "function_call"
    FUNCTION_RESULT = "function_result"
    HOSTED_TOOL_CALL = "hosted_tool_call"
    HOSTED_TOOL_RESULT = "hosted_tool_result"
    TOOL_SEARCH_CALL = "tool_search_call"
    TOOL_SEARCH_OUTPUT = "tool_search_output"
    MCP_LIST_TOOLS = "mcp_list_tools"
    MCP_CALL = "mcp_call"
    MCP_APPROVAL_REQUEST = "mcp_approval_request"
    HANDOFF_CALL = "handoff_call"
    HANDOFF_RESULT = "handoff_result"
    GUARDRAIL_RESULT = "guardrail_result"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESULT = "approval_result"
    WORKSPACE_CHANGE = "workspace_change"
    ARTIFACT = "artifact"
    ERROR = "error"
