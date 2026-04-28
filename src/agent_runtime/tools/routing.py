from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from agent_runtime.core.events import AgentEvent

ToolKind = Literal[
    "local",
    "workspace",
    "mcp",
    "hosted",
    "agent_tool",
    "handoff",
]

ToolRisk = Literal[
    "read_only",
    "workspace_write",
    "command",
    "network",
    "external_side_effect",
    "secret_access",
]

ToolRoutingMode = Literal[
    "explicit",
    "auto",
    "hybrid",
    "model_discovery",
    "disabled",
]


@dataclass(slots=True, frozen=True)
class ToolCandidate:
    ref: str
    name: str
    kind: ToolKind
    description: str
    parameters: dict[str, Any] | None = None
    category: str | None = None
    tags: tuple[str, ...] = ()
    source: str | None = None
    provider: str | None = None
    risk: ToolRisk = "read_only"
    estimated_schema_tokens: int = 0
    requires_approval: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ToolBudget:
    max_visible_tools: int = 12
    max_schema_tokens: int = 8_000
    max_mcp_tools: int = 8
    max_agent_tools: int = 4
    max_handoffs: int = 3


@dataclass(slots=True, frozen=True)
class ToolRoutingSpec:
    mode: ToolRoutingMode = "explicit"
    budget: ToolBudget = field(default_factory=ToolBudget)
    always_include: tuple[str, ...] = ()
    never_include: tuple[str, ...] = ()
    include_categories: tuple[str, ...] = ()
    include_tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()
    allow_late_bind: bool = False
    allow_model_discovery_tools: bool = False
    refresh_each_turn: bool = True
    min_score: float = 0.15
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def coding_defaults(
        cls,
        *,
        mode: ToolRoutingMode = "hybrid",
        max_visible_tools: int = 12,
        max_schema_tokens: int = 8_000,
    ) -> ToolRoutingSpec:
        return cls(
            mode=mode,
            budget=ToolBudget(
                max_visible_tools=max_visible_tools,
                max_schema_tokens=max_schema_tokens,
            ),
            always_include=(
                "workspace:list_files",
                "workspace:read_file",
                "workspace:search_files",
                "workspace:git_diff",
            ),
            include_categories=("coding", "workspace", "repo", "test"),
            allow_model_discovery_tools=(mode in {"hybrid", "model_discovery"}),
            refresh_each_turn=True,
            metadata={"preset": "coding_defaults"},
        )


@dataclass(slots=True, frozen=True)
class ToolSelectionRequest:
    task: str
    current_input: str
    iteration: int
    provider: str
    model: str | None
    candidates: Sequence[ToolCandidate]
    routing: ToolRoutingSpec = field(default_factory=ToolRoutingSpec)
    recent_events: Sequence[AgentEvent] = ()
    active_tool_refs: frozenset[str] = frozenset()
    workspace_summary: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SelectedTool:
    candidate: ToolCandidate
    score: float
    reason: str
    selected_by: str = "selector"


@dataclass(slots=True, frozen=True)
class ToolSelectionResult:
    selected: tuple[SelectedTool, ...]
    discoverable: tuple[ToolCandidate, ...] = ()
    blocked: tuple[ToolCandidate, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ResolvedToolPlan:
    selected_refs: tuple[str, ...]
    local_tools: tuple[str, ...] = ()
    workspace_tools: tuple[str, ...] = ()
    mcp_tools: tuple[str, ...] = ()
    hosted_tools: tuple[Any, ...] = ()
    agent_tools: tuple[str, ...] = ()
    handoffs: tuple[str, ...] = ()
    provider_tools: tuple[dict[str, Any], ...] = ()
    prompt_fragments: tuple[Any, ...] = ()
    fingerprint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolSelector(Protocol):
    async def select(self, request: ToolSelectionRequest) -> ToolSelectionResult:
        ...


def estimate_schema_tokens(payload: dict[str, Any] | None) -> int:
    if not payload:
        return 0
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return max(1, int(len(raw) / 4.0 + 0.5))


def tool_candidate_identity(candidate: ToolCandidate) -> tuple[str, str]:
    return candidate.ref, candidate.name
