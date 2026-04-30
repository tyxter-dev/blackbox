from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from blackbox.core.capabilities import ModelCapabilityProfile
from blackbox.core.results import OutputSpec, OutputStrategy
from blackbox.tools.hosted.specs import HostedToolSpec, hosted_tool_kind
from blackbox.tools.routing import ResolvedToolPlan

if TYPE_CHECKING:
    from blackbox.planning.prompts import PromptBundle, PromptFragment


@dataclass(slots=True, frozen=True)
class ResolvedTool:
    """Executable local tool that is enabled for this run.

    This is the bridge between registry metadata and provider tool schemas. The
    ``definition`` is what adapters send to the model; tags and fragments are
    runtime metadata used for prompt composition and diagnostics.
    """

    name: str
    definition: Mapping[str, Any] = field(default_factory=dict)
    tags: frozenset[str] = field(default_factory=frozenset)
    category: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    prompt_fragments: tuple[PromptFragment, ...] = ()
    hidden: bool = False


@dataclass(slots=True, frozen=True)
class ResolvedHostedTool:
    """Provider-hosted tool enabled for this run."""

    kind: str
    spec: HostedToolSpec
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class ResolvedMCPToolset:
    """MCP server/toolset after routing has been decided for this run.

    ``route`` is usually ``"local"`` or ``"provider_native"``. ``allowed_tools``
    is best-effort metadata for prompt selectors and observability; provider
    native MCP servers may discover the concrete tools later.
    """

    server_label: str
    route: str
    allowed_tools: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class DynamicToolLoadingSpec:
    """Describes a run where the visible tool set may expand during execution."""

    mode: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class DataSourceRef:
    """Reference to external context that influenced the run plan."""

    kind: str
    id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedRunSpec:
    """Single source of truth for a planned runtime invocation.

    The runtime builds this after provider, model, tools, hosted tools, MCP,
    workspace, output, and cache controls have been resolved. Prompt composition
    and executable model/tool configuration both consume this object, which is
    the invariant that prevents prompt/tool drift.
    """

    # Provider/model identity and capability profile.
    provider: str
    model: str | None
    provider_profile: ModelCapabilityProfile

    # User-visible request inputs.
    input: object
    base_instructions: str | None = None
    channel: str | None = None

    # Effective execution surface for this run.
    tools: list[ResolvedTool] = field(default_factory=list)
    hosted_tools: list[ResolvedHostedTool] = field(default_factory=list)
    mcp_toolsets: list[ResolvedMCPToolset] = field(default_factory=list)
    workspace: Any | None = None

    # Output and provider-control choices after capability negotiation.
    output_spec: OutputSpec | None = None
    output_strategy: OutputStrategy | str | None = None
    dynamic_loading: DynamicToolLoadingSpec | None = None
    tool_routing_plan: ResolvedToolPlan | None = None
    cache: Any | None = None

    # Runtime metadata for selectors, observability, and dry-run inspection.
    data_sources: list[DataSourceRef] = field(default_factory=list)
    context_flags: list[str] = field(default_factory=list)
    available_tool_ids: list[str] = field(default_factory=list)
    available_prompt_fragments: list[PromptFragment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    prompt: PromptBundle | None = None

    @property
    def effective_tool_ids(self) -> list[str]:
        return [tool.name for tool in self.tools]

    @property
    def tool_tags(self) -> list[str]:
        tags: set[str] = set()
        for tool in self.tools:
            tags.update(tool.tags)
        return sorted(tags)

    @property
    def hosted_tool_kinds(self) -> list[str]:
        return [tool.kind for tool in self.hosted_tools]

    @property
    def mcp_servers(self) -> list[str]:
        return [toolset.server_label for toolset in self.mcp_toolsets]

    @property
    def mcp_tools(self) -> list[str]:
        names: set[str] = set()
        for toolset in self.mcp_toolsets:
            names.update(toolset.allowed_tools)
        return sorted(names)

    @property
    def workspace_kinds(self) -> list[str]:
        if self.workspace is None:
            return []
        kind = getattr(self.workspace, "kind", None)
        return [kind] if isinstance(kind, str) else []

    @property
    def supports_instructions(self) -> bool:
        detail = self.provider_profile.controls.get("instructions")
        return detail is not None and detail.status != "unsupported"

    @property
    def capability_names(self) -> list[str]:
        names: set[str] = set()
        if self.provider_profile.summary.supports_function_tools:
            names.add("function_tools")
        if self.provider_profile.summary.supports_hosted_tools:
            names.add("hosted_tools")
        for control_name, detail in self.provider_profile.controls.items():
            if detail.status != "unsupported":
                names.add(f"control:{control_name}")
        for hosted_tool_name, detail in self.provider_profile.hosted_tools.items():
            if detail.status != "unsupported":
                names.add(f"hosted_tool:{hosted_tool_name}")
        for output_strategy, detail in self.provider_profile.output_strategies.items():
            if detail.status != "unsupported":
                names.add(f"output_strategy:{output_strategy}")
        return sorted(names)

    @property
    def default_prompt_fragments(self) -> list[PromptFragment]:
        fragments: list[PromptFragment] = []
        for tool in self.tools:
            fragments.extend(tool.prompt_fragments)
        return fragments


def resolved_hosted_tools(specs: list[HostedToolSpec]) -> list[ResolvedHostedTool]:
    return [ResolvedHostedTool(kind=hosted_tool_kind(spec), spec=spec) for spec in specs]
