from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from agent_runtime.core.errors import ConfigurationError

if TYPE_CHECKING:
    from agent_runtime.planning.run_plan import ResolvedRunSpec

PromptPlacement = Literal[
    "system",
    "developer",
    "tool_guidance",
    "output_guidance",
    "dynamic_context",
]
PromptMode = Literal["none", "base", "tool_aware"]
PromptParityMode = Literal["off", "warn", "error"]


def _frozen(values: Any) -> frozenset[str]:
    if values is None:
        return frozenset()
    if isinstance(values, str):
        return frozenset({values})
    return frozenset(str(value) for value in values)


@dataclass(slots=True, frozen=True)
class FragmentSelector:
    """Conditions that decide whether an instruction fragment applies to a run.

    Selectors match against the already-resolved run plan, not against global
    runtime state. For example, ``tools={"create_task"}`` means the fragment is
    selected only when ``create_task`` is in the effective tool list for this
    run. Empty selector fields are ignored, and non-empty fields are combined
    with AND semantics.

    ``context_flags`` is the app/domain escape hatch for facts that are not
    tools, such as ``"customer.exists"`` or ``"channel.whatsapp"``.
    """

    # Tool surfaces.
    tools: frozenset[str] = field(default_factory=frozenset)
    tool_tags: frozenset[str] = field(default_factory=frozenset)
    hosted_tool_kinds: frozenset[str] = field(default_factory=frozenset)
    mcp_servers: frozenset[str] = field(default_factory=frozenset)
    mcp_tools: frozenset[str] = field(default_factory=frozenset)
    workspace_kinds: frozenset[str] = field(default_factory=frozenset)

    # Provider/model and request-mode surfaces.
    providers: frozenset[str] = field(default_factory=frozenset)
    models: frozenset[str] = field(default_factory=frozenset)
    channels: frozenset[str] = field(default_factory=frozenset)
    output_strategies: frozenset[str] = field(default_factory=frozenset)
    dynamic_loading_modes: frozenset[str] = field(default_factory=frozenset)

    # App-provided semantic facts that are not tools or provider capabilities.
    context_flags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        # Public callers can pass str/list/set values; the runtime normalizes
        # them once so matching can stay simple and deterministic.
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, _frozen(getattr(self, name)))

    def is_empty(self) -> bool:
        return all(not getattr(self, name) for name in self.__dataclass_fields__)


@dataclass(slots=True, frozen=True)
class FragmentRequirements:
    """Hard prerequisites for selecting a fragment.

    Selectors answer "is this kind of run relevant?" Requirements answer "can
    this guidance actually be honored?" A fragment about calling
    ``create_customer`` should require that tool so the composer can skip or
    fail before telling the model to do something impossible.
    """

    required_tools: frozenset[str] = field(default_factory=frozenset)
    forbidden_tools: frozenset[str] = field(default_factory=frozenset)
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    forbidden_capabilities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, _frozen(getattr(self, name)))


@dataclass(slots=True, frozen=True)
class PromptFragment:
    """Reusable instruction text plus machine-readable selection metadata.

    Fragments are the unit domain packages and tool authors contribute to the
    prompt. They may be tool-specific, channel-specific, output-strategy
    specific, or tied to app context. The text is model-visible; selectors,
    requirements, conflict groups, cacheability, and metadata remain runtime
    structure for composition, diagnostics, tests, and future evaluators.
    """

    id: str
    text: str
    source: str = "app"
    priority: int = 0
    cacheable: bool = True
    applies_to: FragmentSelector = field(default_factory=FragmentSelector)
    requires: FragmentRequirements = field(default_factory=FragmentRequirements)
    placement: PromptPlacement = "tool_guidance"
    conflict_group: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PromptSpec:
    """Caller controls for prompt composition on one run.

    ``mode`` chooses how much composition occurs. ``base`` preserves caller
    instructions only, while ``tool_aware`` adds matching fragments from tools
    and prompt registries. ``parity`` controls whether prompt/tool drift is
    ignored, reported as warnings, or rejected before the model call.
    """

    mode: PromptMode = "base"
    channel: str | None = None
    cache_sections: bool = True
    parity: PromptParityMode = "warn"
    include_fragment_metadata: bool = True


@dataclass(slots=True, frozen=True)
class PromptSection:
    """One ordered section of the composed prompt.

    Sections keep provider-visible text separate from runtime metadata. That
    lets adapters map stable sections to provider cache controls later without
    forcing private metadata into the model request.
    """

    id: str
    content: str
    placement: PromptPlacement = "system"
    cacheable: bool = True
    source: str = "runtime"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class CacheSection:
    """Cache metadata for a prompt section.

    The content mirrors a ``PromptSection``; this object exists so examples,
    diagnostics, and provider adapters can reason about cache boundaries without
    parsing the final instruction string.
    """

    id: str
    content: str
    cacheable: bool


@dataclass(slots=True, frozen=True)
class SelectedPromptFragment:
    """A fragment selected for a run and the selector fields that matched."""

    fragment: PromptFragment
    matched_on: Mapping[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class SkippedPromptFragment:
    """A fragment considered by the composer but intentionally excluded."""

    fragment: PromptFragment
    reason: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class PromptParityIssue:
    """A prompt/tool consistency finding produced during composition."""

    code: str
    message: str
    severity: Literal["warning", "error"] = "warning"
    fragment_id: str | None = None
    tool_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
        if self.fragment_id is not None:
            payload["fragment_id"] = self.fragment_id
        if self.tool_id is not None:
            payload["tool_id"] = self.tool_id
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(slots=True)
class PromptBundle:
    """Composed prompt artifact returned by planning.

    ``instructions`` is the provider-visible instruction string. The remaining
    fields explain how it was built: section order, selected/skipped fragments,
    cache sections, effective tools, parity issues, and fingerprints. Tests and
    observability should inspect this object instead of scraping strings.
    """

    instructions: str
    input: object
    sections: list[PromptSection] = field(default_factory=list)
    cache_sections: list[CacheSection] = field(default_factory=list)
    selected_fragments: list[SelectedPromptFragment] = field(default_factory=list)
    skipped_fragments: list[SkippedPromptFragment] = field(default_factory=list)
    parity_issues: list[PromptParityIssue] = field(default_factory=list)
    effective_tool_ids: list[str] = field(default_factory=list)
    prompt_fingerprint: str = ""
    parity_fingerprint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class PromptFragmentRegistry:
    """Application/domain registry for reusable runtime-aware fragments.

    Tools can also carry default fragments. This registry is for guidance that
    belongs to an app, vertical package, channel package, or deployment policy.
    """

    def __init__(self) -> None:
        self._fragments: dict[str, PromptFragment] = {}

    def register(
        self,
        fragment: PromptFragment | None = None,
        *,
        tool: str | None = None,
        fragment_id: str | None = None,
        text: str | None = None,
        **kwargs: Any,
    ) -> PromptFragment:
        if fragment is None:
            if fragment_id is None or text is None:
                raise ValueError("fragment_id and text are required when fragment is omitted.")
            selector = kwargs.pop("applies_to", None)
            if selector is None and tool is not None:
                selector = FragmentSelector(tools=frozenset({tool}))
            fragment = PromptFragment(
                id=fragment_id,
                text=text,
                applies_to=selector or FragmentSelector(),
                **kwargs,
            )
        elif tool is not None and tool not in fragment.applies_to.tools:
            fragment = PromptFragment(
                id=fragment.id,
                text=fragment.text,
                source=fragment.source,
                priority=fragment.priority,
                cacheable=fragment.cacheable,
                applies_to=_selector_with_tool(fragment.applies_to, tool),
                requires=fragment.requires,
                placement=fragment.placement,
                conflict_group=fragment.conflict_group,
                metadata=fragment.metadata,
            )
        self._fragments[fragment.id] = fragment
        return fragment

    def all(self) -> list[PromptFragment]:
        return list(self._fragments.values())


class PromptComposer:
    """Builds a ``PromptBundle`` from a resolved run plan.

    The composer is deliberately downstream of tool resolution. It never enables
    tools. It only selects instructions that match the same resolved plan the
    agent loop will execute with, then records why fragments were selected or
    skipped.
    """

    _PLACEMENT_ORDER: ClassVar[dict[str, int]] = {
        "system": 0,
        "developer": 10,
        "tool_guidance": 20,
        "output_guidance": 30,
        "dynamic_context": 40,
    }

    def __init__(self, registry: PromptFragmentRegistry | None = None) -> None:
        self.registry = registry or PromptFragmentRegistry()

    def build(self, plan: ResolvedRunSpec, spec: PromptSpec | None = None) -> PromptBundle:
        """Compose provider-visible instructions and prompt metadata.

        Tool-aware mode selects fragments, applies conflicts, validates parity,
        and records fingerprints for the resolved run plan.
        """
        prompt_spec = spec or PromptSpec()
        selected: list[SelectedPromptFragment] = []
        skipped: list[SkippedPromptFragment] = []

        if prompt_spec.mode == "tool_aware":
            candidates = _unique_fragments(
                [
                    *self.registry.all(),
                    *plan.available_prompt_fragments,
                    *plan.default_prompt_fragments,
                ]
            )
            selected, skipped = self._select_fragments(candidates, plan)
            selected, conflict_skips = self._apply_conflicts(selected)
            skipped.extend(conflict_skips)

        sections: list[PromptSection] = []
        if prompt_spec.mode != "none" and plan.base_instructions:
            sections.append(
                PromptSection(
                    id="base.instructions",
                    content=plan.base_instructions,
                    placement="system",
                    cacheable=True,
                    source="caller",
                )
            )

        generated_sections = [
            PromptSection(
                id=f"fragment.{item.fragment.id}",
                content=item.fragment.text,
                placement=item.fragment.placement,
                cacheable=item.fragment.cacheable,
                source=item.fragment.source,
                metadata={"fragment_id": item.fragment.id},
            )
            for item in selected
        ]
        if not plan.base_instructions and generated_sections and not plan.supports_instructions:
            skipped.extend(
                SkippedPromptFragment(
                    fragment=item.fragment,
                    reason="provider_unsupported_instructions",
                    details={"provider": plan.provider, "model": plan.model},
                )
                for item in selected
            )
            selected = []
            generated_sections = []

        sections.extend(
            sorted(
                generated_sections,
                key=lambda section: (
                    self._PLACEMENT_ORDER.get(section.placement, 100),
                    section.id,
                ),
            )
        )

        instructions = "\n\n".join(section.content for section in sections)
        cache_sections = [
            CacheSection(id=section.id, content=section.content, cacheable=section.cacheable)
            for section in sections
        ]
        issues = validate_prompt_tool_parity(
            instructions=instructions,
            plan=plan,
            selected=selected,
            skipped=skipped,
            parity=prompt_spec.parity,
        )
        prompt_fingerprint = _fingerprint(
            {
                "instructions": instructions,
                "sections": [section.id for section in sections],
                "fragments": [item.fragment.id for item in selected],
            }
        )
        parity_fingerprint = _fingerprint(
            {
                "tools": plan.effective_tool_ids,
                "hosted_tools": plan.hosted_tool_kinds,
                "fragments": [item.fragment.id for item in selected],
                "issues": [issue.to_dict() for issue in issues],
            }
        )
        parity_status = (
            "fail"
            if any(issue.severity == "error" for issue in issues)
            else "warn"
            if issues
            else "pass"
        )
        mentioned_tool_ids = _mentioned_tool_ids(instructions, plan.available_tool_ids)
        disabled_tool_mentions = sorted(set(mentioned_tool_ids) - set(plan.effective_tool_ids))
        metadata: dict[str, Any] = {
            "mode": prompt_spec.mode,
            "channel": prompt_spec.channel or plan.channel,
            "fragment_ids": [item.fragment.id for item in selected],
            "skipped_fragment_ids": [item.fragment.id for item in skipped],
            "effective_tool_ids": list(plan.effective_tool_ids),
            "available_tool_ids": list(plan.available_tool_ids),
            "mentioned_tool_ids": mentioned_tool_ids,
            "disabled_tool_mentions": disabled_tool_mentions,
            "parity": parity_status,
            "prompt_fingerprint": prompt_fingerprint,
            "parity_fingerprint": parity_fingerprint,
        }
        if prompt_spec.include_fragment_metadata:
            metadata["selected_fragments"] = [
                {
                    "id": item.fragment.id,
                    "source": item.fragment.source,
                    "priority": item.fragment.priority,
                    "cacheable": item.fragment.cacheable,
                    "placement": item.fragment.placement,
                    "matched_on": dict(item.matched_on),
                }
                for item in selected
            ]
            metadata["skipped_fragments"] = [
                {
                    "id": item.fragment.id,
                    "source": item.fragment.source,
                    "reason": item.reason,
                    "details": dict(item.details),
                }
                for item in skipped
            ]
        if issues:
            metadata["parity_issues"] = [issue.to_dict() for issue in issues]

        bundle = PromptBundle(
            instructions=instructions,
            input=plan.input,
            sections=sections,
            cache_sections=cache_sections if prompt_spec.cache_sections else [],
            selected_fragments=selected,
            skipped_fragments=skipped,
            parity_issues=issues,
            effective_tool_ids=list(plan.effective_tool_ids),
            prompt_fingerprint=prompt_fingerprint,
            parity_fingerprint=parity_fingerprint,
            metadata=metadata,
        )
        if prompt_spec.parity == "error":
            assert_prompt_tool_parity(bundle)
        return bundle

    def _select_fragments(
        self, fragments: list[PromptFragment], plan: ResolvedRunSpec
    ) -> tuple[list[SelectedPromptFragment], list[SkippedPromptFragment]]:
        selected: list[SelectedPromptFragment] = []
        skipped: list[SkippedPromptFragment] = []
        for fragment in fragments:
            matched = _selector_matches(fragment.applies_to, plan)
            if matched is None:
                skipped.append(
                    SkippedPromptFragment(
                        fragment=fragment,
                        reason="selector_not_matched",
                        details=_selector_mismatch_details(fragment.applies_to, plan),
                    )
                )
                continue
            requirements_issue = _requirements_issue(fragment.requires, plan)
            if requirements_issue is not None:
                skipped.append(
                    SkippedPromptFragment(
                        fragment=fragment,
                        reason=requirements_issue[0],
                        details=requirements_issue[1],
                    )
                )
                continue
            selected.append(SelectedPromptFragment(fragment=fragment, matched_on=matched))
        selected.sort(key=lambda item: (-item.fragment.priority, item.fragment.id))
        return selected, skipped

    def _apply_conflicts(
        self, selected: list[SelectedPromptFragment]
    ) -> tuple[list[SelectedPromptFragment], list[SkippedPromptFragment]]:
        winners: dict[str, SelectedPromptFragment] = {}
        keep: list[SelectedPromptFragment] = []
        skipped: list[SkippedPromptFragment] = []
        for item in selected:
            group = item.fragment.conflict_group
            if group is None:
                keep.append(item)
                continue
            current = winners.get(group)
            if current is None:
                winners[group] = item
                continue
            challenger_key = (item.fragment.priority, item.fragment.id)
            current_key = (current.fragment.priority, current.fragment.id)
            if challenger_key > current_key:
                skipped.append(
                    SkippedPromptFragment(
                        fragment=current.fragment,
                        reason="conflict_lost",
                        details={"conflict_group": group, "winner": item.fragment.id},
                    )
                )
                winners[group] = item
            else:
                skipped.append(
                    SkippedPromptFragment(
                        fragment=item.fragment,
                        reason="conflict_lost",
                        details={"conflict_group": group, "winner": current.fragment.id},
                    )
                )
        keep.extend(winners.values())
        keep.sort(
            key=lambda item: (
                self._PLACEMENT_ORDER.get(item.fragment.placement, 100),
                -item.fragment.priority,
                item.fragment.id,
            )
        )
        return keep, skipped


def validate_prompt_tool_parity(
    *,
    instructions: str,
    plan: ResolvedRunSpec,
    selected: list[SelectedPromptFragment],
    skipped: list[SkippedPromptFragment],
    parity: PromptParityMode,
) -> list[PromptParityIssue]:
    """Return issues when prompt guidance references unavailable tools."""
    if parity == "off":
        return []
    severity: Literal["warning", "error"] = "error" if parity == "error" else "warning"
    issues: list[PromptParityIssue] = []
    effective = set(plan.effective_tool_ids)
    available = set(plan.available_tool_ids)
    disabled = available - effective

    for selected_item in selected:
        required = set(selected_item.fragment.requires.required_tools)
        missing = sorted(required - effective)
        for tool_id in missing:
            issues.append(
                PromptParityIssue(
                    code="fragment_required_tool_missing",
                    message=(
                        f"Fragment {selected_item.fragment.id!r} "
                        f"requires disabled tool {tool_id!r}."
                    ),
                    severity=severity,
                    fragment_id=selected_item.fragment.id,
                    tool_id=tool_id,
                )
            )

    for skipped_item in skipped:
        if skipped_item.reason == "required_tools_missing":
            missing_tools = skipped_item.details.get("missing", [])
            for tool_id in missing_tools if isinstance(missing_tools, list) else []:
                issues.append(
                    PromptParityIssue(
                        code="fragment_skipped_required_tool_missing",
                        message=(
                            f"Fragment {skipped_item.fragment.id!r} was skipped "
                            f"because {tool_id!r} is unavailable."
                        ),
                        severity=severity,
                        fragment_id=skipped_item.fragment.id,
                        tool_id=str(tool_id),
                    )
                )

    for tool_id in sorted(disabled):
        if _mentions_tool(instructions, tool_id):
            issues.append(
                PromptParityIssue(
                    code="prompt_mentions_disabled_tool",
                    message=f"Prompt mentions disabled tool {tool_id!r}.",
                    severity=severity,
                    tool_id=tool_id,
                )
            )
    return issues


def _selector_with_tool(selector: FragmentSelector, tool: str) -> FragmentSelector:
    return FragmentSelector(
        tools=frozenset({*selector.tools, tool}),
        tool_tags=selector.tool_tags,
        hosted_tool_kinds=selector.hosted_tool_kinds,
        mcp_servers=selector.mcp_servers,
        mcp_tools=selector.mcp_tools,
        workspace_kinds=selector.workspace_kinds,
        providers=selector.providers,
        models=selector.models,
        channels=selector.channels,
        output_strategies=selector.output_strategies,
        dynamic_loading_modes=selector.dynamic_loading_modes,
        context_flags=selector.context_flags,
    )


def assert_prompt_tool_parity(bundle: PromptBundle) -> None:
    """Raise ConfigurationError when a prompt bundle has parity errors."""
    errors = [issue for issue in bundle.parity_issues if issue.severity == "error"]
    if not errors:
        return
    joined = "; ".join(issue.message for issue in errors)
    raise ConfigurationError(f"Prompt/tool parity failed: {joined}")


def _selector_matches(
    selector: FragmentSelector, plan: ResolvedRunSpec
) -> Mapping[str, list[str]] | None:
    if selector.is_empty():
        return {}
    checks: list[tuple[str, frozenset[str], frozenset[str]]] = [
        ("tools", selector.tools, frozenset(plan.effective_tool_ids)),
        ("tool_tags", selector.tool_tags, frozenset(plan.tool_tags)),
        ("hosted_tool_kinds", selector.hosted_tool_kinds, frozenset(plan.hosted_tool_kinds)),
        ("mcp_servers", selector.mcp_servers, frozenset(plan.mcp_servers)),
        ("mcp_tools", selector.mcp_tools, frozenset(plan.mcp_tools)),
        ("workspace_kinds", selector.workspace_kinds, frozenset(plan.workspace_kinds)),
        ("providers", selector.providers, frozenset({plan.provider})),
        ("models", selector.models, frozenset({plan.model} if plan.model else set())),
        ("channels", selector.channels, frozenset({plan.channel} if plan.channel else set())),
        (
            "output_strategies",
            selector.output_strategies,
            frozenset({plan.output_strategy} if plan.output_strategy else set()),
        ),
        (
            "dynamic_loading_modes",
            selector.dynamic_loading_modes,
            frozenset({plan.dynamic_loading.mode} if plan.dynamic_loading else set()),
        ),
        ("context_flags", selector.context_flags, frozenset(plan.context_flags)),
    ]
    matched: dict[str, list[str]] = {}
    for name, wanted, actual in checks:
        if not wanted:
            continue
        intersection = sorted(wanted & actual)
        if not intersection:
            return None
        matched[name] = intersection
    return matched


def _requirements_issue(
    requirements: FragmentRequirements, plan: ResolvedRunSpec
) -> tuple[str, Mapping[str, Any]] | None:
    effective = set(plan.effective_tool_ids)
    missing = sorted(set(requirements.required_tools) - effective)
    if missing:
        return "required_tools_missing", {"missing": missing}
    forbidden_present = sorted(set(requirements.forbidden_tools) & effective)
    if forbidden_present:
        return "forbidden_tools_present", {"present": forbidden_present}
    capabilities = set(plan.capability_names)
    missing_caps = sorted(set(requirements.required_capabilities) - capabilities)
    if missing_caps:
        return "required_capabilities_missing", {"missing": missing_caps}
    forbidden_caps = sorted(set(requirements.forbidden_capabilities) & capabilities)
    if forbidden_caps:
        return "forbidden_capabilities_present", {"present": forbidden_caps}
    return None


def _selector_mismatch_details(
    selector: FragmentSelector, plan: ResolvedRunSpec
) -> Mapping[str, Any]:
    if selector.is_empty():
        return {}
    checks: list[tuple[str, frozenset[str], frozenset[str]]] = [
        ("tools", selector.tools, frozenset(plan.effective_tool_ids)),
        ("tool_tags", selector.tool_tags, frozenset(plan.tool_tags)),
        ("hosted_tool_kinds", selector.hosted_tool_kinds, frozenset(plan.hosted_tool_kinds)),
        ("mcp_servers", selector.mcp_servers, frozenset(plan.mcp_servers)),
        ("mcp_tools", selector.mcp_tools, frozenset(plan.mcp_tools)),
        ("workspace_kinds", selector.workspace_kinds, frozenset(plan.workspace_kinds)),
        ("providers", selector.providers, frozenset({plan.provider})),
        ("models", selector.models, frozenset({plan.model} if plan.model else set())),
        ("channels", selector.channels, frozenset({plan.channel} if plan.channel else set())),
        (
            "output_strategies",
            selector.output_strategies,
            frozenset({plan.output_strategy} if plan.output_strategy else set()),
        ),
        (
            "dynamic_loading_modes",
            selector.dynamic_loading_modes,
            frozenset({plan.dynamic_loading.mode} if plan.dynamic_loading else set()),
        ),
        ("context_flags", selector.context_flags, frozenset(plan.context_flags)),
    ]
    details: dict[str, dict[str, list[str]]] = {}
    for name, wanted, actual in checks:
        if wanted and not wanted.intersection(actual):
            details[name] = {"wanted": sorted(wanted), "actual": sorted(actual)}
    return details


def _mentions_tool(text: str, tool_id: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(tool_id)}(?![A-Za-z0-9_])", text) is not None


def _mentioned_tool_ids(text: str, tool_ids: list[str]) -> list[str]:
    return [tool_id for tool_id in tool_ids if _mentions_tool(text, tool_id)]


def _unique_fragments(fragments: list[PromptFragment]) -> list[PromptFragment]:
    unique: dict[str, PromptFragment] = {}
    for fragment in fragments:
        unique[fragment.id] = fragment
    return list(unique.values())


def _fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
