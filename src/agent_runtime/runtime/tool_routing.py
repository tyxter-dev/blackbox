from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.policy import Policy, PolicyDecision, PolicyRequest
from agent_runtime.mcp import MCPToolset
from agent_runtime.tools.hosted.specs import HostedToolSpec
from agent_runtime.tools.registry import ToolRegistry
from agent_runtime.tools.routing import (
    ResolvedToolPlan,
    SelectedTool,
    ToolCandidate,
    ToolKind,
    ToolRisk,
    ToolRoutingSpec,
    ToolSelectionRequest,
    ToolSelectionResult,
)
from agent_runtime.tools.sources import (
    dedupe_by_ref,
    hosted_tool_candidates,
    local_tool_candidates,
    mcp_tool_candidates,
)

_RISK_ORDER: dict[ToolRisk, int] = {
    "read_only": 0,
    "workspace_write": 1,
    "command": 2,
    "network": 3,
    "external_side_effect": 4,
    "secret_access": 5,
}

_SOURCE_ORDER: dict[ToolKind, int] = {
    "workspace": 0,
    "local": 1,
    "mcp": 2,
    "hosted": 3,
    "agent_tool": 4,
    "handoff": 5,
}


@dataclass(slots=True)
class RoutingContext:
    task: str
    current_input: str
    iteration: int
    provider: str
    model: str | None
    registry: ToolRegistry
    routing: ToolRoutingSpec
    hosted_tools: Sequence[HostedToolSpec] = ()
    mcp_toolsets: Sequence[MCPToolset] = ()
    policy: Policy | None = None
    recent_events: Sequence[AgentEvent] = ()
    active_tool_refs: frozenset[str] = frozenset()
    workspace_summary: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RoutingResolution:
    selection: ToolSelectionResult
    plan: ResolvedToolPlan
    events: tuple[AgentEvent, ...]


class DefaultToolSelector:
    async def select(self, request: ToolSelectionRequest) -> ToolSelectionResult:
        """Select, block, or defer tool candidates using routing rules and budgets."""

        routing = request.routing
        if routing.mode == "disabled":
            return ToolSelectionResult(selected=(), discoverable=tuple(request.candidates))

        scored: list[SelectedTool] = []
        blocked: list[ToolCandidate] = []
        discoverable: list[ToolCandidate] = []
        for candidate in request.candidates:
            if _is_never_included(candidate, routing):
                blocked.append(candidate)
                continue
            if _has_excluded_tag(candidate, routing):
                blocked.append(candidate)
                continue
            selected = _score_candidate(candidate, request)
            if _is_always_included(candidate, routing):
                scored.append(
                    SelectedTool(
                        candidate=candidate,
                        score=max(selected.score, 1.0),
                        reason="always_include",
                    )
                )
                continue
            if routing.mode == "model_discovery":
                discoverable.append(candidate)
                continue
            if selected.score >= routing.min_score:
                scored.append(selected)
            else:
                discoverable.append(candidate)

        packed, budget_blocked = _pack_budget(scored, routing)
        blocked.extend(budget_blocked)
        discoverable.extend(
            selected.candidate
            for selected in scored
            if selected.candidate.ref not in {tool.candidate.ref for tool in packed}
            and selected.candidate not in budget_blocked
        )
        metadata = {
            "candidate_count": len(request.candidates),
            "selected_count": len(packed),
            "blocked_count": len(blocked),
            "discoverable_count": len(discoverable),
            "iteration": request.iteration,
        }
        return ToolSelectionResult(
            selected=tuple(packed),
            discoverable=tuple(dedupe_by_ref(discoverable)),
            blocked=tuple(dedupe_by_ref(blocked)),
            metadata=metadata,
        )


async def collect_candidates(context: RoutingContext) -> list[ToolCandidate]:
    candidates: list[ToolCandidate] = []
    candidates.extend(local_tool_candidates(context.registry))
    candidates.extend(hosted_tool_candidates(context.hosted_tools))
    candidates.extend(await mcp_tool_candidates(context.mcp_toolsets))
    return dedupe_by_ref(candidates)


async def resolve_tool_routing(
    context: RoutingContext,
    *,
    provider_tools_by_name: Mapping[str, dict[str, Any]] | None = None,
) -> RoutingResolution:
    """Collect candidates, apply policy, and return a resolved routing plan."""

    started = AgentEvent(
        type=EventTypes.TOOL_ROUTING_STARTED,
        provider=context.provider,
        data={
            "mode": context.routing.mode,
            "iteration": context.iteration,
            "active_tool_refs": sorted(context.active_tool_refs),
        },
    )
    try:
        candidates = await collect_candidates(context)
        allowed, policy_blocked = await _policy_filter_candidates(
            candidates,
            policy=context.policy,
        )
        request = ToolSelectionRequest(
            task=context.task,
            current_input=context.current_input,
            iteration=context.iteration,
            provider=context.provider,
            model=context.model,
            candidates=allowed,
            routing=context.routing,
            recent_events=context.recent_events,
            active_tool_refs=context.active_tool_refs,
            workspace_summary=context.workspace_summary,
            metadata=context.metadata,
        )
        selection = await DefaultToolSelector().select(request)
        if policy_blocked:
            selection = ToolSelectionResult(
                selected=selection.selected,
                discoverable=selection.discoverable,
                blocked=(*selection.blocked, *policy_blocked),
                metadata={
                    **selection.metadata,
                    "policy_blocked_count": len(policy_blocked),
                },
            )
        plan = build_resolved_tool_plan(
            selection,
            provider_tools_by_name=provider_tools_by_name or {},
            metadata={
                "mode": context.routing.mode,
                "iteration": context.iteration,
                "candidate_count": len(candidates),
            },
        )
        completed = _routing_completed_event(
            context=context,
            candidates=candidates,
            selection=selection,
            plan=plan,
        )
        return RoutingResolution(selection=selection, plan=plan, events=(started, completed))
    except Exception as exc:
        failed = AgentEvent(
            type=EventTypes.TOOL_ROUTING_FAILED,
            provider=context.provider,
            data={
                "mode": context.routing.mode,
                "iteration": context.iteration,
                "error": str(exc),
            },
        )
        return RoutingResolution(
            selection=ToolSelectionResult(selected=()),
            plan=ResolvedToolPlan(selected_refs=()),
            events=(started, failed),
        )


def build_resolved_tool_plan(
    selection: ToolSelectionResult,
    *,
    provider_tools_by_name: Mapping[str, dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> ResolvedToolPlan:
    """Convert a tool selection result into a provider-facing resolved plan."""

    selected_refs = tuple(tool.candidate.ref for tool in selection.selected)
    local_tools = _names_for_kind(selection.selected, "local")
    workspace_tools = _names_for_kind(selection.selected, "workspace")
    mcp_tools = _names_for_kind(selection.selected, "mcp")
    agent_tools = _names_for_kind(selection.selected, "agent_tool")
    handoffs = _names_for_kind(selection.selected, "handoff")
    provider_tools = tuple(
        dict(provider_tools_by_name[candidate.name])
        for candidate in (tool.candidate for tool in selection.selected)
        if candidate.kind in {"local", "workspace", "mcp", "agent_tool", "handoff"}
        and candidate.name in provider_tools_by_name
    )
    plan_metadata = {
        **(metadata or {}),
        "blocked_refs": [candidate.ref for candidate in selection.blocked],
        "discoverable_refs": [candidate.ref for candidate in selection.discoverable],
        "estimated_schema_tokens": sum(
            tool.candidate.estimated_schema_tokens for tool in selection.selected
        ),
    }
    fingerprint = _plan_fingerprint(
        {
            "selected_refs": list(selected_refs),
            "local_tools": list(local_tools),
            "workspace_tools": list(workspace_tools),
            "mcp_tools": list(mcp_tools),
            "blocked_refs": plan_metadata["blocked_refs"],
        }
    )
    return ResolvedToolPlan(
        selected_refs=selected_refs,
        local_tools=local_tools,
        workspace_tools=workspace_tools,
        mcp_tools=mcp_tools,
        agent_tools=agent_tools,
        handoffs=handoffs,
        provider_tools=provider_tools,
        fingerprint=fingerprint,
        metadata=plan_metadata,
    )


def selected_tool_names(plan: ResolvedToolPlan) -> set[str]:
    return {
        *plan.local_tools,
        *plan.workspace_tools,
        *plan.mcp_tools,
        *plan.agent_tools,
        *plan.handoffs,
    }


def tool_routing_late_bound_event(
    *,
    provider: str,
    iteration: int,
    candidate: ToolCandidate,
    fingerprint: str | None,
) -> AgentEvent:
    return AgentEvent(
        type=EventTypes.TOOL_ROUTING_LATE_BOUND,
        provider=provider,
        data={
            "iteration": iteration,
            "ref": candidate.ref,
            "name": candidate.name,
            "kind": candidate.kind,
            "fingerprint": fingerprint,
        },
    )


def _score_candidate(
    candidate: ToolCandidate,
    request: ToolSelectionRequest,
) -> SelectedTool:
    """Heuristically score a candidate against task context, recency, and risk."""

    task_text = (request.current_input or request.task).lower()
    score = 0.0
    reasons: list[str] = []

    if candidate.ref.lower() in task_text or candidate.name.lower() in task_text:
        score += 0.40
        reasons.append("exact_match")

    task_tokens = _tokens(task_text)
    name_tokens = _tokens(candidate.name.replace("_", " ").replace(":", " "))
    description_tokens = _tokens(candidate.description)
    tag_tokens = {tag.lower() for tag in candidate.tags}
    category = (candidate.category or "").lower()
    if task_tokens.intersection(name_tokens):
        score += 0.30
        reasons.append("name_match")
    if task_tokens.intersection(description_tokens):
        score += 0.25
        reasons.append("description_match")
    if category and category in request.routing.include_categories:
        score += 0.20
        reasons.append("category_match")
    if tag_tokens.intersection(tag.lower() for tag in request.routing.include_tags):
        score += 0.20
        reasons.append("tag_match")
    if candidate.kind == "workspace" and request.workspace_summary:
        score += 0.20
        reasons.append("workspace_context")
    if candidate.ref in request.active_tool_refs or candidate.name in request.active_tool_refs:
        score += 0.10
        reasons.append("active")
    if _recent_failure(candidate, request.recent_events):
        score -= 0.30
        reasons.append("recent_failure")
    if candidate.risk in {"command", "network", "external_side_effect", "secret_access"}:
        score -= 0.20
        reasons.append("risk_penalty")
    if candidate.requires_approval:
        score -= 0.10
        reasons.append("approval_penalty")

    if not reasons:
        reasons.append("default")
    return SelectedTool(candidate=candidate, score=max(0.0, score), reason=",".join(reasons))


def _pack_budget(
    scored: list[SelectedTool],
    routing: ToolRoutingSpec,
) -> tuple[list[SelectedTool], list[ToolCandidate]]:
    always_refs = set(routing.always_include)
    ordered = sorted(
        scored,
        key=lambda selected: (
            0 if _candidate_matches_any(selected.candidate, always_refs) else 1,
            -selected.score,
            _RISK_ORDER[selected.candidate.risk],
            selected.candidate.estimated_schema_tokens,
            _SOURCE_ORDER[selected.candidate.kind],
            selected.candidate.ref,
        ),
    )
    selected: list[SelectedTool] = []
    blocked: list[ToolCandidate] = []
    schema_tokens = 0
    kind_counts: dict[ToolKind, int] = {
        "local": 0,
        "workspace": 0,
        "mcp": 0,
        "hosted": 0,
        "agent_tool": 0,
        "handoff": 0,
    }
    for item in ordered:
        candidate = item.candidate
        is_always = _candidate_matches_any(candidate, always_refs)
        if not is_always and len(selected) >= routing.budget.max_visible_tools:
            blocked.append(candidate)
            continue
        if not is_always and schema_tokens + candidate.estimated_schema_tokens > routing.budget.max_schema_tokens:
            blocked.append(candidate)
            continue
        if not is_always and candidate.kind == "mcp" and kind_counts["mcp"] >= routing.budget.max_mcp_tools:
            blocked.append(candidate)
            continue
        if not is_always and candidate.kind == "agent_tool" and kind_counts["agent_tool"] >= routing.budget.max_agent_tools:
            blocked.append(candidate)
            continue
        if not is_always and candidate.kind == "handoff" and kind_counts["handoff"] >= routing.budget.max_handoffs:
            blocked.append(candidate)
            continue
        selected.append(item)
        kind_counts[candidate.kind] += 1
        schema_tokens += candidate.estimated_schema_tokens
    return selected, blocked


async def _policy_filter_candidates(
    candidates: Sequence[ToolCandidate],
    *,
    policy: Policy | None,
) -> tuple[list[ToolCandidate], list[ToolCandidate]]:
    if policy is None:
        return list(candidates), []
    allowed: list[ToolCandidate] = []
    blocked: list[ToolCandidate] = []
    for candidate in candidates:
        decision: PolicyDecision = await policy.check(
            PolicyRequest(
                checkpoint="before_tool_exposure",
                action=candidate.name,
                metadata={
                    "ref": candidate.ref,
                    "kind": candidate.kind,
                    "category": candidate.category,
                    "tags": list(candidate.tags),
                    "risk": candidate.risk,
                    "tool_metadata": dict(candidate.metadata),
                },
            )
        )
        if decision.verdict == "allow":
            allowed.append(candidate)
        else:
            blocked.append(candidate)
    return allowed, blocked


def _routing_completed_event(
    *,
    context: RoutingContext,
    candidates: Sequence[ToolCandidate],
    selection: ToolSelectionResult,
    plan: ResolvedToolPlan,
) -> AgentEvent:
    return AgentEvent(
        type=EventTypes.TOOL_ROUTING_COMPLETED,
        provider=context.provider,
        data={
            "mode": context.routing.mode,
            "iteration": context.iteration,
            "candidate_count": len(candidates),
            "selected_count": len(selection.selected),
            "selected_refs": list(plan.selected_refs),
            "blocked_refs": [candidate.ref for candidate in selection.blocked],
            "budget": {
                "max_visible_tools": context.routing.budget.max_visible_tools,
                "max_schema_tokens": context.routing.budget.max_schema_tokens,
                "estimated_schema_tokens": plan.metadata.get("estimated_schema_tokens", 0),
            },
            "fingerprint": plan.fingerprint,
            "selected_names": sorted(selected_tool_names(plan)),
        },
    )


def _names_for_kind(
    selected: Iterable[SelectedTool],
    kind: ToolKind,
) -> tuple[str, ...]:
    return tuple(tool.candidate.name for tool in selected if tool.candidate.kind == kind)


def _is_always_included(candidate: ToolCandidate, routing: ToolRoutingSpec) -> bool:
    return _candidate_matches_any(candidate, set(routing.always_include))


def _is_never_included(candidate: ToolCandidate, routing: ToolRoutingSpec) -> bool:
    return _candidate_matches_any(candidate, set(routing.never_include))


def _candidate_matches_any(candidate: ToolCandidate, refs: set[str]) -> bool:
    return bool(refs.intersection({candidate.ref, candidate.name}))


def _has_excluded_tag(candidate: ToolCandidate, routing: ToolRoutingSpec) -> bool:
    excluded = {tag.lower() for tag in routing.exclude_tags}
    return bool(excluded.intersection(tag.lower() for tag in candidate.tags))


def _recent_failure(candidate: ToolCandidate, events: Sequence[AgentEvent]) -> bool:
    for event in events[-20:]:
        if event.type not in {EventTypes.TOOL_CALL_FAILED, EventTypes.TOOL_CHOICE_FAILED}:
            continue
        if event.data.get("name") in {candidate.name, candidate.ref}:
            return True
    return False


def _tokens(value: str) -> set[str]:
    normalized = value.lower().replace("_", " ").replace("-", " ").replace(":", " ")
    return {token for token in normalized.split() if len(token) >= 3}


def _plan_fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()[:24]
