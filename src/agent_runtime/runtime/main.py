from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, TypeVar, cast
from uuid import uuid4

from agent_runtime.core.accounting import (
    ModelCatalog,
    add_usage,
    usage_provider_details,
    usage_to_dict,
)
from agent_runtime.core.approvals import ApprovalDecision
from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.cache import InMemoryProviderCacheStore, ProviderCacheStore
from agent_runtime.core.capabilities import ModelCapabilityProfile, get_model_capability_profile
from agent_runtime.core.errors import (
    ApprovalError,
    ConfigurationError,
    OutputValidationError,
    ProviderExecutionError,
    ToolExecutionError,
)
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import RunItem
from agent_runtime.core.policy import Policy, PolicyDecision, PolicyRequest
from agent_runtime.core.results import AgentResult, OutputSpec, OutputStrategy, ToolPayload
from agent_runtime.core.state import ProviderState
from agent_runtime.core.stores import (
    EventStore,
    InMemoryEventStore,
    InMemoryRunStore,
    InMemorySessionStore,
    RunStore,
    SessionStore,
)
from agent_runtime.mcp import MCPConnector, MCPToolset, resolve_mcp_route, to_remote_mcp
from agent_runtime.observability.traces import TraceContext, trace_metadata_from_events
from agent_runtime.output.schema import OutputSchema, build_output_schema
from agent_runtime.planning.prompts import (
    PromptComposer,
    PromptFragmentRegistry,
    PromptMode,
    PromptSpec,
)
from agent_runtime.planning.run_plan import (
    DataSourceRef,
    DynamicToolLoadingSpec,
    ResolvedMCPToolset,
    ResolvedRunSpec,
    resolved_hosted_tools,
)
from agent_runtime.providers.base import CompactionControl, ModelCacheControl, ToolSearchControl
from agent_runtime.providers.model_adapters.capability_validation import resolve_output_strategy
from agent_runtime.providers.registry import ProviderRef, ProviderRegistry
from agent_runtime.runtime.agents import AgentRuntimeFacade
from agent_runtime.runtime.caches import ProviderCacheRuntime
from agent_runtime.runtime.chat import ChatRuntimeFacade
from agent_runtime.runtime.event_metadata import (
    _attach_accounting_metadata,
    _event_usage,
    _hosted_tool_metadata_from_events,
    _mcp_metadata_from_events,
    _prompt_metadata_from_events,
    _provider_cache_metadata,
    _tool_choice_metadata_from_events,
    _tool_names,
    _tool_routing_metadata_from_events,
    _tool_usage_from_events,
)
from agent_runtime.runtime.model import ModelRuntime
from agent_runtime.runtime.output import (
    _FINALIZER_TOOL_NAME,
    _build_repair_prompt,
    _can_fallback_provider_native,
    _finalizer_tool_payload,
    _resolve_output_spec,
    _validate_output,
)
from agent_runtime.runtime.prompting import PromptRuntimeFacade
from agent_runtime.runtime.run_planning import (
    _dynamic_loading_from_controls,
    _prompt_events,
    _resolve_prompt_spec,
    _resolved_mcp_toolset,
    _resolved_tools,
)
from agent_runtime.runtime.tool_routing import (
    RoutingContext,
    collect_candidates,
    resolve_tool_routing,
    selected_tool_names,
    tool_routing_late_bound_event,
)
from agent_runtime.runtime.tools import ToolRuntimeFacade
from agent_runtime.runtime.workspace_results import _workspace_metadata_from_events
from agent_runtime.runtime.workspaces import WorkspaceRuntimeFacade
from agent_runtime.tools.catalog import ToolCatalog
from agent_runtime.tools.hosted.specs import HostedToolHandlers, HostedToolSpec
from agent_runtime.tools.registry import ToolDefinition
from agent_runtime.tools.routing import ResolvedToolPlan, ToolCandidate, ToolRoutingSpec
from agent_runtime.tools.runtime import ToolRuntime
from agent_runtime.tools.session import ToolSession
from agent_runtime.tools.toolsets import (
    DynamicToolsetSession,
    ToolBudget,
    ToolSelection,
    Toolset,
)

T = TypeVar("T")

class AgentRuntime:
    """Top-level runtime.

    Exposes:
      * ``runtime.run(...)`` and ``runtime.stream(...)`` — high-level blackbox
        loop that owns tool dispatch, continuation, and structured output
        validation. This is the spiritual successor to the v1 product promise.
      * ``runtime.tools`` — the local tool registry facade used by the
        high-level path.
      * ``runtime.models`` and ``runtime.agents`` — lower-level supervision
        facades for direct model turns and agent sessions.
    """

    def __init__(
        self,
        registry: ProviderRegistry | None = None,
        *,
        event_store: EventStore | None = None,
        run_store: RunStore | None = None,
        session_store: SessionStore | None = None,
        provider_cache_store: ProviderCacheStore | None = None,
    ) -> None:
        self.registry = registry or ProviderRegistry()
        self.model_catalog = ModelCatalog()
        self.event_store: EventStore = event_store or InMemoryEventStore()
        self.run_store: RunStore = run_store or InMemoryRunStore()
        self.session_store: SessionStore = session_store or InMemorySessionStore()
        self.provider_cache_store: ProviderCacheStore = (
            provider_cache_store or InMemoryProviderCacheStore()
        )
        self.models = ModelRuntime(
            self.registry,
            model_catalog=self.model_catalog,
            provider_cache_store=self.provider_cache_store,
        )
        self.prompt_fragments = PromptFragmentRegistry()
        self.prompt_composer = PromptComposer(self.prompt_fragments)
        self.prompts = PromptRuntimeFacade(self)
        self.chat = ChatRuntimeFacade(self.models)
        self.workspaces = WorkspaceRuntimeFacade()
        self.tools = ToolRuntimeFacade()
        self.agents = AgentRuntimeFacade(
            self.registry,
            event_store=self.event_store,
            run_store=self.run_store,
            session_store=self.session_store,
            workspaces=self.workspaces,
        )
        self.caches = ProviderCacheRuntime(self.registry, self.provider_cache_store)
        from agent_runtime.realtime.runtime import RealtimeRuntime

        self.realtime = RealtimeRuntime(
            self.registry,
            event_store=self.event_store,
            tool_registry=self.tools.registry,
        )
        self._active_loops: dict[str, Any] = {}

    async def close(self) -> None:
        """Release resources held by registered provider adapters."""
        await self.registry.close()

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        """Approve a pending high-level ``runtime.run/stream`` operation."""
        for loop in list(self._active_loops.values()):
            if approval_id in loop._approvals:
                await loop.approve(approval_id, decision)
                return
        if approval_id.startswith("workspace_approval_"):
            try:
                await self.workspaces.approve(approval_id, decision)
                return
            except Exception:
                pass
        try:
            await self.realtime.approve(approval_id, decision)
            return
        except ApprovalError:
            pass
        raise ApprovalError(f"No pending approval with id '{approval_id}'.")

    def model_capabilities(
        self,
        provider: str,
        *,
        model: str | None = None,
    ) -> ModelCapabilityProfile:
        return self.models.capabilities(provider, model=model)

    async def plan_run(
        self,
        *,
        provider: str,
        input: str,
        model: str | None = None,
        tools: list[str] | str | None = None,
        tool_routing: ToolRoutingSpec | None = None,
        tool_session: ToolSession | None = None,
        instructions: str | None = None,
        prompt: PromptSpec | None = None,
        prompt_mode: PromptMode | None = None,
        channel: str | None = None,
        output_schema: OutputSchema | None = None,
        output_strategy: OutputStrategy | None = None,
        output_spec: OutputSpec | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        toolsets: list[Any] | None = None,
        tool_selection: ToolSelection = "static",
        tool_budget: ToolBudget | None = None,
        cache: ModelCacheControl | None = None,
        tool_search: ToolSearchControl | None = None,
        dynamic_loading: DynamicToolLoadingSpec | None = None,
        data_sources: list[DataSourceRef] | None = None,
        context_flags: list[str] | None = None,
        workspace: Any | None = None,
        workspace_provider: Any | None = None,
        workspace_policy: Any = None,
        workspace_prefix: str = "workspace",
        policy: Any = None,
    ) -> ResolvedRunSpec:
        """Resolve a run without executing it and return the prompt/tool plan."""
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        capability_profile = get_model_capability_profile(adapter, model_name)
        effective_output_schema = output_schema
        effective_output_strategy = output_strategy
        if output_spec is not None:
            effective_output_schema = build_output_schema(output_spec)
            effective_output_strategy = output_strategy or output_spec.strategy
        if effective_output_schema is not None and effective_output_strategy is not None:
            effective_output_strategy = resolve_output_strategy(
                profile=capability_profile,
                requested=effective_output_strategy,
                fallback=output_spec.fallback if output_spec is not None else "posthoc_parse",
            )
            if effective_output_strategy == "posthoc_parse":
                effective_output_schema = None

        effective_tool_session = tool_session
        effective_tools, effective_tool_routing = _normalize_tools_argument(
            tools,
            tool_routing=tool_routing,
        )
        effective_hosted_tools = list(hosted_tools or [])
        resolved_mcp_toolsets: list[ResolvedMCPToolset] = []
        active_mcp_connectors: list[MCPConnector] = []
        opened_workspace: tuple[Any, Any, bool] | None = None
        budget = tool_budget or ToolBudget()
        mcp_toolsets, local_toolsets = self._split_toolsets(toolsets)
        try:
            if workspace is not None:
                effective_tool_session = (
                    ToolSession.from_registry(tool_session.registry)
                    if tool_session is not None
                    else self.tools.session()
                )
                workspace_provider_obj, workspace_ref, should_close = await self._open_runtime_workspace(
                    workspace,
                    provider=workspace_provider,
                    policy=workspace_policy or policy,
                )
                opened_workspace = (workspace_provider_obj, workspace_ref, should_close)
                registered_workspace_tools = effective_tool_session.register_workspace(
                    workspace_provider_obj,
                    workspace_ref,
                    prefix=workspace_prefix,
                )
                effective_tools.extend(tool.name for tool in registered_workspace_tools)

            local_tool_names: list[str] = []
            if local_toolsets:
                effective_tool_session = (
                    ToolSession.from_registry(effective_tool_session.registry)
                    if effective_tool_session is not None
                    else self.tools.session()
                )
                local_tool_names = self._register_local_toolsets(
                    effective_tool_session,
                    local_toolsets,
                )

            mcp_local_tool_names: list[str] = []
            for mcp_toolset in mcp_toolsets:
                route = resolve_mcp_route(
                    provider_ref,
                    mcp_toolset,
                    capability_profile,
                    policy=policy,
                )
                if route == "provider_native":
                    effective_hosted_tools.append(to_remote_mcp(mcp_toolset))
                    resolved_mcp_toolsets.append(
                        _resolved_mcp_toolset(mcp_toolset, route="provider_native")
                    )
                    continue
                effective_tool_session = (
                    ToolSession.from_registry(effective_tool_session.registry)
                    if effective_tool_session is not None
                    else self.tools.session()
                )
                connector = MCPConnector(
                    [mcp_toolset.server_for_local_dispatch()],
                    policy=None,
                )
                registered_mcp_tools = await connector.register_runtime_tools(
                    effective_tool_session.registry
                )
                mcp_local_tool_names.extend(tool.name for tool in registered_mcp_tools)
                resolved_mcp_toolsets.append(
                    _resolved_mcp_toolset(
                        mcp_toolset,
                        route="local",
                        allowed_tools=[tool.name for tool in registered_mcp_tools],
                    )
                )
                active_mcp_connectors.append(connector)

            dynamic_loading_spec: DynamicToolLoadingSpec | None
            if _routing_enabled(effective_tool_routing):
                routing_spec = effective_tool_routing
                assert routing_spec is not None
                effective_tools.extend([*local_tool_names, *mcp_local_tool_names])
                effective_tool_session = effective_tool_session or self.tools.session()
                provider_tools_by_name = {
                    str(tool["name"]): dict(tool)
                    for tool in effective_tool_session.to_provider_tools()
                    if isinstance(tool.get("name"), str)
                }
                resolution = await resolve_tool_routing(
                    RoutingContext(
                        task=input,
                        current_input=input,
                        iteration=0,
                        provider=provider_ref.provider_key,
                        model=model_name,
                        registry=effective_tool_session.registry,
                        routing=routing_spec,
                        hosted_tools=effective_hosted_tools,
                        mcp_toolsets=mcp_toolsets,
                        policy=policy,
                    ),
                    provider_tools_by_name=provider_tools_by_name,
                )
                effective_tools = sorted(selected_tool_names(resolution.plan))
                tool_definitions = self._tool_payload(
                    effective_tools,
                    tool_session=effective_tool_session,
                )
                dynamic_loading_spec = dynamic_loading or DynamicToolLoadingSpec(
                    mode=routing_spec.mode,
                    metadata={
                        "routing": True,
                        "fingerprint": resolution.plan.fingerprint,
                    },
                )
                routing_plan: ResolvedToolPlan | None = resolution.plan
            elif self._should_use_dynamic_tools(
                selection=tool_selection,
                tool_session=effective_tool_session,
                budget=budget,
            ):
                effective_tool_session = effective_tool_session or self.tools.session()
                dynamic_session = await self._dynamic_tool_session(
                    tool_session=effective_tool_session,
                    initial_visible=effective_tools,
                    budget=budget,
                    policy=policy,
                )
                tool_definitions = dynamic_session.provider_tools()
                dynamic_loading_spec = dynamic_loading or DynamicToolLoadingSpec(
                    mode="runtime_toolset",
                    metadata={
                        "catalog_size": len(dynamic_session.catalog.entries),
                        "max_tools_visible": budget.max_tools_visible,
                    },
                )
                routing_plan = None
            else:
                effective_tools.extend([*local_tool_names, *mcp_local_tool_names])
                effective_tools, _ = await self._filter_tools_for_exposure(
                    effective_tools,
                    tool_session=effective_tool_session,
                    policy=policy,
                    budget=budget,
                )
                tool_definitions = self._tool_payload(
                    effective_tools,
                    tool_session=effective_tool_session,
                )
                dynamic_loading_spec = dynamic_loading or _dynamic_loading_from_controls(
                    tool_search=tool_search,
                    hosted_tools=effective_hosted_tools,
                    mcp_toolsets=resolved_mcp_toolsets,
                )
                routing_plan = None

            if (
                effective_output_schema is not None
                and effective_output_strategy == "finalizer_tool"
            ):
                tool_definitions.append(_finalizer_tool_payload(effective_output_schema))

            prompt_spec = _resolve_prompt_spec(
                prompt,
                prompt_mode=prompt_mode,
                channel=channel,
            )
            plan = self._resolved_run_spec(
                provider_ref=provider_ref,
                model_name=model_name,
                capability_profile=capability_profile,
                input=input,
                instructions=instructions,
                prompt_spec=prompt_spec,
                tool_definitions=tool_definitions,
                effective_hosted_tools=effective_hosted_tools,
                mcp_toolsets=resolved_mcp_toolsets,
                workspace=opened_workspace[1] if opened_workspace is not None else None,
                output_spec=output_spec,
                output_strategy=effective_output_strategy,
                cache=cache,
                dynamic_loading=dynamic_loading_spec,
                tool_routing_plan=routing_plan,
                data_sources=data_sources,
                context_flags=context_flags,
                tool_session=effective_tool_session,
            )
            plan.prompt = self.prompt_composer.build(plan, prompt_spec)
            return plan
        finally:
            if opened_workspace is not None:
                workspace_provider_obj, workspace_ref, should_close = opened_workspace
                if should_close:
                    await workspace_provider_obj.close(workspace_ref)
            for connector in active_mcp_connectors:
                await connector.stop()

    async def stream(
        self,
        *,
        provider: str,
        input: str,
        model: str | None = None,
        tools: list[str] | str | None = None,
        tool_routing: ToolRoutingSpec | None = None,
        tool_session: ToolSession | None = None,
        tool_execution_context: dict[str, Any] | None = None,
        tool_max_concurrent: int | None = None,
        tool_timeout: float | None = None,
        approval_policy: Any = None,
        policy: Any = None,
        max_iterations: int = 8,
        mock_tools: bool = False,
        provider_state: ProviderState | None = None,
        instructions: str | None = None,
        prompt: PromptSpec | None = None,
        prompt_mode: PromptMode | None = None,
        channel: str | None = None,
        output_schema: OutputSchema | None = None,
        output_strategy: OutputStrategy | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        toolsets: list[Any] | None = None,
        tool_selection: ToolSelection = "static",
        tool_budget: ToolBudget | None = None,
        hosted_tool_handlers: HostedToolHandlers | None = None,
        cache: ModelCacheControl | None = None,
        tool_search: ToolSearchControl | None = None,
        dynamic_loading: DynamicToolLoadingSpec | None = None,
        compaction: CompactionControl | None = None,
        modalities: list[str] | None = None,
        data_sources: list[DataSourceRef] | None = None,
        context_flags: list[str] | None = None,
        workspace: Any | None = None,
        workspace_provider: Any | None = None,
        workspace_policy: Any = None,
        workspace_prefix: str = "workspace",
        workspace_preserve: bool = False,
        **kwargs: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Stream events from a complete agent loop driven by the registered model.

        ``tools`` is a list of registered tool names to expose to the model.
        Local tool calls are dispatched through ``runtime.tools`` automatically
        and their results are fed back via provider-native continuation.

        Pass ``provider_state`` to resume from saved state (e.g. one loaded
        from :class:`SQLiteRunStore`).
        """
        from agent_runtime.runtime.agent_loop import AgentLoop

        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        capability_profile = get_model_capability_profile(adapter, model_name)

        effective_tool_session = tool_session
        effective_tools, effective_tool_routing = _normalize_tools_argument(
            tools,
            tool_routing=tool_routing,
        )
        effective_hosted_tools = list(hosted_tools or [])
        resolved_mcp_toolsets: list[ResolvedMCPToolset] = []
        active_mcp_connectors: list[MCPConnector] = []
        opened_workspace: tuple[Any, Any, bool] | None = None
        budget = tool_budget or ToolBudget()
        mcp_toolsets, local_toolsets = self._split_toolsets(toolsets)
        tool_choice_events: list[AgentEvent] = []
        if workspace is not None:
            effective_tool_session = (
                ToolSession.from_registry(tool_session.registry)
                if tool_session is not None
                else self.tools.session()
            )
            workspace_provider_obj, workspace_ref, should_close = await self._open_runtime_workspace(
                workspace,
                provider=workspace_provider,
                policy=workspace_policy or policy,
            )
            opened_workspace = (workspace_provider_obj, workspace_ref, should_close)
            registered_workspace_tools = effective_tool_session.register_workspace(
                workspace_provider_obj,
                workspace_ref,
                prefix=workspace_prefix,
            )
            effective_tools.extend(tool.name for tool in registered_workspace_tools)

        local_tool_names: list[str] = []
        if local_toolsets:
            effective_tool_session = (
                ToolSession.from_registry(effective_tool_session.registry)
                if effective_tool_session is not None
                else self.tools.session()
            )
            local_tool_names = self._register_local_toolsets(
                effective_tool_session,
                local_toolsets,
            )

        mcp_local_tool_names: list[str] = []
        for mcp_toolset in mcp_toolsets:
            route = resolve_mcp_route(
                provider_ref,
                mcp_toolset,
                capability_profile,
                policy=policy,
            )
            if route == "provider_native":
                effective_hosted_tools.append(to_remote_mcp(mcp_toolset))
                resolved_mcp_toolsets.append(
                    _resolved_mcp_toolset(mcp_toolset, route="provider_native")
                )
                continue
            effective_tool_session = (
                ToolSession.from_registry(effective_tool_session.registry)
                if effective_tool_session is not None
                else self.tools.session()
            )
            connector = MCPConnector(
                [mcp_toolset.server_for_local_dispatch()],
                policy=None,
            )
            registered_mcp_tools = await connector.register_runtime_tools(
                effective_tool_session.registry
            )
            tool_choice_events.extend(connector.drain_events())
            mcp_local_tool_names.extend(tool.name for tool in registered_mcp_tools)
            resolved_mcp_toolsets.append(
                _resolved_mcp_toolset(
                    mcp_toolset,
                    route="local",
                    allowed_tools=[tool.name for tool in registered_mcp_tools],
                )
            )
            active_mcp_connectors.append(connector)

        dynamic_session: DynamicToolsetSession | None = None
        dynamic_loading_spec: DynamicToolLoadingSpec | None
        routing_plan: ResolvedToolPlan | None = None
        routing_events: list[AgentEvent] = []
        active_tool_definitions: list[dict[str, Any]] = []
        active_tool_names: set[str] = set()
        provider_tools_by_name: dict[str, dict[str, Any]] = {}
        if _routing_enabled(effective_tool_routing):
            routing_spec = effective_tool_routing
            assert routing_spec is not None
            effective_tools.extend([*local_tool_names, *mcp_local_tool_names])
            effective_tool_session = effective_tool_session or self.tools.session()
            provider_tools_by_name = {
                str(tool["name"]): dict(tool)
                for tool in effective_tool_session.to_provider_tools()
                if isinstance(tool.get("name"), str)
            }
            resolution = await resolve_tool_routing(
                RoutingContext(
                    task=input,
                    current_input=input,
                    iteration=0,
                    provider=provider_ref.provider_key,
                    model=model_name,
                    registry=effective_tool_session.registry,
                    routing=routing_spec,
                    hosted_tools=effective_hosted_tools,
                    mcp_toolsets=mcp_toolsets,
                    policy=policy,
                    workspace_summary=_workspace_summary(
                        opened_workspace[1] if opened_workspace is not None else None
                    ),
                ),
                provider_tools_by_name=provider_tools_by_name,
            )
            routing_plan = resolution.plan
            routing_events.extend(resolution.events)
            effective_tools = sorted(selected_tool_names(routing_plan))
            active_tool_names = set(effective_tools)
            active_tool_definitions = self._tool_payload(
                effective_tools,
                tool_session=effective_tool_session,
            )
            tool_definitions = list(active_tool_definitions)
            dynamic_loading_spec = dynamic_loading or DynamicToolLoadingSpec(
                mode=routing_spec.mode,
                metadata={
                    "routing": True,
                    "fingerprint": routing_plan.fingerprint,
                },
            )
        elif self._should_use_dynamic_tools(
            selection=tool_selection,
            tool_session=effective_tool_session,
            budget=budget,
        ):
            effective_tool_session = effective_tool_session or self.tools.session()
            dynamic_session = await self._dynamic_tool_session(
                tool_session=effective_tool_session,
                initial_visible=effective_tools,
                budget=budget,
                policy=policy,
            )
            tool_choice_events.extend(dynamic_session.initial_events())
            tool_definitions = dynamic_session.provider_tools()
            effective_tools = [
                tool["name"]
                for tool in tool_definitions
                if isinstance(tool.get("name"), str)
            ]
            dynamic_loading_spec = dynamic_loading or DynamicToolLoadingSpec(
                mode="runtime_toolset",
                metadata={
                    "catalog_size": len(dynamic_session.catalog.entries),
                    "max_tools_visible": budget.max_tools_visible,
                },
            )
        else:
            effective_tools.extend([*local_tool_names, *mcp_local_tool_names])
            effective_tools, exposure_events = await self._filter_tools_for_exposure(
                effective_tools,
                tool_session=effective_tool_session,
                policy=policy,
                budget=budget,
            )
            tool_choice_events.extend(exposure_events)
            tool_definitions = self._tool_payload(
                effective_tools,
                tool_session=effective_tool_session,
            )
            active_tool_names = set(effective_tools)
            active_tool_definitions = list(tool_definitions)
            dynamic_loading_spec = dynamic_loading or _dynamic_loading_from_controls(
                tool_search=tool_search,
                hosted_tools=effective_hosted_tools,
                mcp_toolsets=resolved_mcp_toolsets,
            )
        if output_schema is not None and output_strategy == "finalizer_tool":
            tool_definitions.append(_finalizer_tool_payload(output_schema))
        allowed_tool_names = (
            dynamic_session.visible_names
            if dynamic_session is not None
            else {
                tool["name"]
                for tool in tool_definitions
                if isinstance(tool.get("name"), str)
            }
        )
        runtime_allowed_tools = (
            set(allowed_tool_names)
            if _routing_enabled(effective_tool_routing)
            else (set(allowed_tool_names) if allowed_tool_names else None)
        )
        tool_runtime = self._tool_runtime_for(
            tool_execution_context,
            tool_session=effective_tool_session,
            max_concurrent=tool_max_concurrent
            if tool_max_concurrent is not None
            else budget.max_parallel_calls,
            timeout=tool_timeout,
            allowed_tools=runtime_allowed_tools,
        )

        routing_history_events: list[AgentEvent] = []

        async def refresh_routing_plan(
            iteration: int,
            turn_input: str | list[Any],
            _provider_state: ProviderState | None,
        ) -> list[AgentEvent]:
            """Recompute active tool routing for follow-up turns."""

            nonlocal active_tool_definitions, active_tool_names, routing_plan
            if not _routing_enabled(effective_tool_routing):
                return []
            routing_spec = effective_tool_routing
            assert routing_spec is not None
            if iteration == 0 or not routing_spec.refresh_each_turn:
                return []
            if effective_tool_session is None:
                return []
            resolution = await resolve_tool_routing(
                RoutingContext(
                    task=input,
                    current_input=_routing_input_text(turn_input),
                    iteration=iteration,
                    provider=provider_ref.provider_key,
                    model=model_name,
                    registry=effective_tool_session.registry,
                    routing=routing_spec,
                    hosted_tools=effective_hosted_tools,
                    mcp_toolsets=mcp_toolsets,
                    policy=policy,
                    recent_events=tuple(routing_history_events),
                    active_tool_refs=frozenset(
                        routing_plan.selected_refs if routing_plan is not None else ()
                    ),
                    workspace_summary=_workspace_summary(
                        opened_workspace[1] if opened_workspace is not None else None
                    ),
                ),
                provider_tools_by_name=provider_tools_by_name,
            )
            routing_plan = resolution.plan
            active_tool_names = selected_tool_names(routing_plan)
            active_tool_definitions = self._tool_payload(
                sorted(active_tool_names),
                tool_session=effective_tool_session,
            )
            tool_runtime.allowed_tools = set(active_tool_names)
            return list(resolution.events)

        async def late_bind_tool(name: str, iteration: int) -> tuple[bool, list[AgentEvent]]:
            """Expose an exact-match tool during execution when late binding is enabled."""

            nonlocal active_tool_definitions, active_tool_names, routing_plan
            if (
                not _routing_enabled(effective_tool_routing)
                or effective_tool_session is None
            ):
                return False, []
            routing_spec = effective_tool_routing
            assert routing_spec is not None
            if not routing_spec.allow_late_bind:
                return False, []
            candidates = await collect_candidates(
                RoutingContext(
                    task=input,
                    current_input=name,
                    iteration=iteration,
                    provider=provider_ref.provider_key,
                    model=model_name,
                    registry=effective_tool_session.registry,
                    routing=routing_spec,
                    hosted_tools=effective_hosted_tools,
                    mcp_toolsets=mcp_toolsets,
                    policy=policy,
                )
            )
            candidate = _find_candidate(candidates, name)
            if candidate is None:
                return False, []
            active_tool_names.add(candidate.name)
            active_tool_definitions = self._tool_payload(
                sorted(active_tool_names),
                tool_session=effective_tool_session,
            )
            tool_runtime.allowed_tools = set(active_tool_names)
            event = tool_routing_late_bound_event(
                provider=provider_ref.provider_key,
                iteration=iteration,
                candidate=candidate,
                fingerprint=routing_plan.fingerprint if routing_plan is not None else None,
            )
            return True, [event]

        prompt_spec = _resolve_prompt_spec(prompt, prompt_mode=prompt_mode, channel=channel)
        run_plan = self._resolved_run_spec(
            provider_ref=provider_ref,
            model_name=model_name,
            capability_profile=capability_profile,
            input=input,
            instructions=instructions,
            prompt_spec=prompt_spec,
            tool_definitions=tool_definitions,
            effective_hosted_tools=effective_hosted_tools,
            mcp_toolsets=resolved_mcp_toolsets,
            workspace=opened_workspace[1] if opened_workspace is not None else None,
            output_strategy=output_strategy,
            cache=cache,
            dynamic_loading=dynamic_loading_spec,
            tool_routing_plan=routing_plan,
            data_sources=data_sources,
            context_flags=context_flags,
            tool_session=effective_tool_session,
        )
        try:
            prompt_bundle = self.prompt_composer.build(run_plan, prompt_spec)
        except Exception:
            if opened_workspace is not None and not workspace_preserve:
                workspace_provider_obj, workspace_ref, should_close = opened_workspace
                if should_close:
                    await workspace_provider_obj.close(workspace_ref)
            for connector in active_mcp_connectors:
                await connector.stop()
            raise
        run_plan.prompt = prompt_bundle
        static_tool_definitions = [dict(tool.definition) for tool in run_plan.tools]
        effective_hosted_tools = [tool.spec for tool in run_plan.hosted_tools]

        run_id = f"run_{uuid4().hex}"
        trace_context = TraceContext.for_run(run_id)

        def current_tool_definitions() -> list[dict[str, Any]]:
            if _routing_enabled(effective_tool_routing):
                definitions = [dict(tool) for tool in active_tool_definitions]
                if output_schema is not None and output_strategy == "finalizer_tool":
                    definitions.append(_finalizer_tool_payload(output_schema))
                return definitions
            if dynamic_session is not None:
                definitions = dynamic_session.provider_tools()
                if output_schema is not None and output_strategy == "finalizer_tool":
                    definitions.append(_finalizer_tool_payload(output_schema))
                return definitions
            return [dict(tool) for tool in static_tool_definitions]

        async def stream_factory(
            *, input: str | list[Any], provider_state: ProviderState | None
        ) -> AsyncIterator[AgentEvent]:
            async for event in self.models.stream(
                provider=provider,
                model=model_name,
                input=input,
                provider_state=provider_state,
                tools=current_tool_definitions(),
                hosted_tools=effective_hosted_tools,
                output_schema=output_schema,
                output_strategy=output_strategy,
                instructions=prompt_bundle.instructions or None,
                cache=cache,
                tool_search=tool_search,
                compaction=compaction,
                modalities=modalities,
                run_id=run_id,
                trace_id=trace_context.trace_id,
                parent_span_id=trace_context.root_span_id,
                **kwargs,
            ):
                yield event

        loop = AgentLoop(
            stream_factory=stream_factory,
            tools=(
                tool_runtime
                if effective_tools
                or _routing_enabled(effective_tool_routing)
                or (output_schema is not None and output_strategy == "finalizer_tool")
                else None
            ),
            hosted_tools=effective_hosted_tools,
            hosted_tool_handlers=hosted_tool_handlers or HostedToolHandlers(),
            approval_policy=approval_policy,
            policy=policy,
            max_iterations=max_iterations,
            max_tool_calls=budget.max_tool_calls,
            finalizer_tool_name=(
                _FINALIZER_TOOL_NAME
                if output_schema is not None and output_strategy == "finalizer_tool"
                else None
            ),
            tool_plan_refresh=(
                refresh_routing_plan
                if _routing_enabled(effective_tool_routing)
                else None
            ),
            tool_late_binder=(
                late_bind_tool
                if _routing_enabled(effective_tool_routing)
                else None
            ),
        )
        self._active_loops[run_id] = loop
        try:
            sequence = 0
            for event in _prompt_events(run_plan, prompt_bundle):
                stamped = trace_context.stamp(
                    event,
                    run_id=run_id,
                    sequence=sequence,
                    preserve_sequence=False,
                )
                sequence += 1
                await self.event_store.append(stamped)
                routing_history_events.append(stamped)
                yield stamped
            for event in routing_events:
                stamped = trace_context.stamp(
                    event,
                    run_id=run_id,
                    sequence=sequence,
                    preserve_sequence=False,
                )
                sequence += 1
                await self.event_store.append(stamped)
                routing_history_events.append(stamped)
                yield stamped
            for event in tool_choice_events:
                stamped = trace_context.stamp(
                    event,
                    run_id=run_id,
                    sequence=sequence,
                    preserve_sequence=False,
                )
                sequence += 1
                await self.event_store.append(stamped)
                routing_history_events.append(stamped)
                yield stamped
            if opened_workspace is not None:
                workspace_provider_obj, _, _ = opened_workspace
                for event in workspace_provider_obj.drain_events():
                    stamped = trace_context.stamp(
                        event,
                        run_id=run_id,
                        sequence=sequence,
                        preserve_sequence=False,
                    )
                    sequence += 1
                    await self.event_store.append(stamped)
                    routing_history_events.append(stamped)
                    yield stamped

            async for event in loop.run(
                input=input,
                provider_state=provider_state,
                provider_id=provider_ref.provider_key,
                mock_tools=mock_tools,
            ):
                stamped = trace_context.stamp(
                    event,
                    run_id=run_id,
                    sequence=sequence,
                    preserve_sequence=False,
                )
                sequence += 1
                await self.event_store.append(stamped)
                routing_history_events.append(stamped)
                yield stamped
            if opened_workspace is not None and not workspace_preserve:
                workspace_provider_obj, workspace_ref, should_close = opened_workspace
                if should_close:
                    await workspace_provider_obj.close(workspace_ref)
                    for event in workspace_provider_obj.drain_events():
                        stamped = trace_context.stamp(
                            event,
                            run_id=run_id,
                            sequence=sequence,
                            preserve_sequence=False,
                        )
                        sequence += 1
                        await self.event_store.append(stamped)
                        routing_history_events.append(stamped)
                        yield stamped
        finally:
            self._active_loops.pop(run_id, None)
            for connector in active_mcp_connectors:
                await connector.stop()

    async def run(
        self,
        *,
        provider: str,
        input: str,
        model: str | None = None,
        tools: list[str] | str | None = None,
        tool_routing: ToolRoutingSpec | None = None,
        tool_session: ToolSession | None = None,
        tool_execution_context: dict[str, Any] | None = None,
        tool_max_concurrent: int | None = None,
        tool_timeout: float | None = None,
        approval_policy: Any = None,
        policy: Any = None,
        max_iterations: int = 8,
        mock_tools: bool = False,
        output_type: type[T] | None = None,
        output_spec: OutputSpec | None = None,
        provider_state: ProviderState | None = None,
        instructions: str | None = None,
        prompt: PromptSpec | None = None,
        prompt_mode: PromptMode | None = None,
        channel: str | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        toolsets: list[Any] | None = None,
        tool_selection: ToolSelection = "static",
        tool_budget: ToolBudget | None = None,
        hosted_tool_handlers: HostedToolHandlers | None = None,
        cache: ModelCacheControl | None = None,
        tool_search: ToolSearchControl | None = None,
        dynamic_loading: DynamicToolLoadingSpec | None = None,
        compaction: CompactionControl | None = None,
        modalities: list[str] | None = None,
        data_sources: list[DataSourceRef] | None = None,
        context_flags: list[str] | None = None,
        workspace: Any | None = None,
        workspace_provider: Any | None = None,
        workspace_policy: Any = None,
        workspace_prefix: str = "workspace",
        workspace_preserve: bool = False,
        **kwargs: Any,
    ) -> AgentResult[T]:
        """Run the complete agent loop and return a typed AgentResult.

        When ``output_type`` (or ``output_spec``) is provided, the final text is
        validated against the schema. Supported schemas: Pydantic models,
        dataclasses, and ``str``. Validation failures raise
        :class:`OutputValidationError`.

        Pass ``output_spec=OutputSpec(schema=Cls,
        strategy="posthoc_parse_with_retry", max_validation_retries=N)`` to ask
        the runtime to feed validation errors back to the model and retry up to
        ``N`` times before failing.
        """
        spec = _resolve_output_spec(output_type, output_spec)
        output_schema = build_output_schema(spec)
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        effective_strategy: OutputStrategy | None = spec.strategy
        initial_provider_native_fallback: str | None = None
        if output_schema is not None:
            profile = get_model_capability_profile(adapter, model_name)
            resolved_strategy = resolve_output_strategy(
                profile=profile,
                requested=spec.strategy,
                fallback=spec.fallback,
            )
            if resolved_strategy != spec.strategy:
                initial_provider_native_fallback = resolved_strategy
            effective_strategy = resolved_strategy
            if effective_strategy == "posthoc_parse":
                output_schema = None
        attempts_allowed = (
            1 + spec.max_validation_retries
            if effective_strategy == "posthoc_parse_with_retry"
            else 1
        )

        events: list[AgentEvent] = []
        items: list[RunItem] = []
        artifacts: list[Artifact] = []
        payloads: list[ToolPayload] = []
        captured_state: ProviderState | None = provider_state
        usage = None
        completed_provider: str | None = None
        completed_model: str | None = None
        last_text = ""
        finalizer_output: dict[str, Any] | None = None
        last_error: OutputValidationError | None = None
        current_input = input
        provider_native_fallback: str | None = initial_provider_native_fallback

        for attempt in range(attempts_allowed):
            text_parts: list[str] = []
            while True:
                try:
                    async for event in self.stream(
                        provider=provider,
                        input=current_input,
                        model=model,
                        tools=tools,
                        tool_routing=tool_routing,
                        tool_session=tool_session,
                        tool_execution_context=tool_execution_context,
                        tool_max_concurrent=tool_max_concurrent,
                        tool_timeout=tool_timeout,
                        approval_policy=approval_policy,
                        policy=policy,
                        max_iterations=max_iterations,
                        mock_tools=mock_tools,
                        provider_state=captured_state,
                        instructions=instructions,
                        prompt=prompt,
                        prompt_mode=prompt_mode,
                        channel=channel,
                        hosted_tools=hosted_tools,
                        toolsets=toolsets,
                        tool_selection=tool_selection,
                        tool_budget=tool_budget,
                        hosted_tool_handlers=hosted_tool_handlers,
                        output_schema=output_schema,
                        output_strategy=effective_strategy if output_schema is not None else None,
                        cache=cache,
                        tool_search=tool_search,
                        dynamic_loading=dynamic_loading,
                        compaction=compaction,
                        modalities=modalities,
                        data_sources=data_sources,
                        context_flags=context_flags,
                        workspace=workspace,
                        workspace_provider=workspace_provider,
                        workspace_policy=workspace_policy,
                        workspace_prefix=workspace_prefix,
                        workspace_preserve=workspace_preserve,
                        **kwargs,
                    ):
                        events.append(event)
                        if event.type == EventTypes.MODEL_TEXT_DELTA:
                            delta = event.data.get("delta")
                            if isinstance(delta, str):
                                text_parts.append(delta)
                        elif event.type == EventTypes.TOOL_CALL_COMPLETED:
                            if "payload" in event.data:
                                payloads.append(
                                    ToolPayload(
                                        tool_name=event.data.get("name", ""),
                                        payload=event.data["payload"],
                                        call_id=event.data.get("call_id"),
                                    )
                                )
                        elif event.type == EventTypes.ARTIFACT_CREATED:
                            artifact = event.data.get("artifact")
                            if isinstance(artifact, Artifact):
                                artifacts.append(artifact)
                        else:
                            artifact = event.data.get("artifact")
                            if isinstance(artifact, Artifact):
                                artifacts.append(artifact)
                        item = event.data.get("item")
                        if isinstance(item, RunItem):
                            items.append(item)
                        event_metadata = event.data.get("metadata")
                        if (
                            event.type == EventTypes.TOOL_CALL_COMPLETED
                            and isinstance(event_metadata, dict)
                            and event_metadata.get("purpose") == "final_output"
                        ):
                            arguments = event.data.get("arguments")
                            if isinstance(arguments, dict):
                                finalizer_output = arguments
                        maybe_state = event.data.get("provider_state")
                        if isinstance(maybe_state, ProviderState):
                            captured_state = maybe_state
                        if event.type == EventTypes.MODEL_COMPLETED:
                            usage = add_usage(usage, _event_usage(event))
                            completed_provider = event.provider or completed_provider
                            model_value = event.data.get("model")
                            if isinstance(model_value, str):
                                completed_model = model_value
                    break
                except ProviderExecutionError:
                    if not _can_fallback_provider_native(
                        effective_strategy=effective_strategy,
                        output_schema=output_schema,
                        fallback=spec.fallback,
                        already_used=provider_native_fallback is not None,
                    ):
                        raise
                    provider_native_fallback = spec.fallback
                    effective_strategy = cast(OutputStrategy, spec.fallback)
                    if effective_strategy == "posthoc_parse":
                        output_schema = None
                    text_parts = []
                    continue

            last_text = (
                json.dumps(finalizer_output)
                if finalizer_output is not None
                else "".join(text_parts)
            )
            try:
                output = _validate_output(
                    last_text,
                    spec.schema,
                    allow_dict_schema=effective_strategy in {"provider_native", "finalizer_tool"},
                )
            except OutputValidationError as exc:
                last_error = exc
                if attempt + 1 >= attempts_allowed:
                    raise
                current_input = _build_repair_prompt(
                    previous_text=last_text, error=exc, attempt=attempt + 1
                )
                continue

            usage = add_usage(usage, _tool_usage_from_events(events))
            metadata: dict[str, Any] = {"validation_attempts": attempt + 1}
            if provider_native_fallback is not None:
                metadata["provider_native_fallback"] = provider_native_fallback
            usage_dict = usage_to_dict(usage)
            if usage_dict is not None:
                metadata["usage"] = usage_dict
            provider_usage = usage_provider_details(usage)
            if provider_usage is not None:
                metadata["usage_provider_details"] = provider_usage
            if usage is not None and completed_provider is not None and completed_model is not None:
                cost = self.model_catalog.estimate_cost(
                    provider=completed_provider, model=completed_model, usage=usage
                )
                if cost is not None:
                    metadata["cost"] = cost
            cache_metadata = await _provider_cache_metadata(
                provider=completed_provider,
                model=completed_model,
                cache=cache,
                usage=usage,
                provider_cache_store=self.provider_cache_store,
            )
            if cache_metadata:
                metadata["cache"] = cache_metadata
            mcp_metadata = _mcp_metadata_from_events(events)
            if mcp_metadata:
                metadata["mcp"] = mcp_metadata
                if "trust" in mcp_metadata:
                    metadata["mcp_trust"] = mcp_metadata["trust"]
            hosted_metadata = _hosted_tool_metadata_from_events(events)
            if hosted_metadata:
                metadata["hosted_tools"] = hosted_metadata
            workspace_metadata = _workspace_metadata_from_events(events)
            if workspace_metadata:
                metadata["workspaces"] = workspace_metadata
            prompt_metadata = _prompt_metadata_from_events(events)
            if prompt_metadata:
                metadata["prompt"] = prompt_metadata
            tool_choice_metadata = _tool_choice_metadata_from_events(events)
            if tool_choice_metadata:
                metadata["tool_choice"] = tool_choice_metadata
            tool_routing_metadata = _tool_routing_metadata_from_events(events)
            if tool_routing_metadata:
                metadata["tool_routing"] = tool_routing_metadata
            trace_metadata = trace_metadata_from_events(events, metadata=metadata)
            if trace_metadata["spans"]:
                metadata["trace"] = trace_metadata
            _attach_accounting_metadata(metadata)
            return AgentResult(
                output=cast(T, output),
                text=last_text,
                events=events,
                items=items,
                artifacts=artifacts,
                payloads=payloads,
                provider_state=captured_state,
                metadata=metadata,
            )

        # Should be unreachable: the loop either returns or re-raises.
        assert last_error is not None
        raise last_error

    def _resolved_run_spec(
        self,
        *,
        provider_ref: ProviderRef,
        model_name: str,
        capability_profile: ModelCapabilityProfile,
        input: object,
        instructions: str | None,
        prompt_spec: PromptSpec,
        tool_definitions: list[dict[str, Any]],
        effective_hosted_tools: list[HostedToolSpec],
        mcp_toolsets: list[ResolvedMCPToolset],
        workspace: Any | None,
        output_spec: OutputSpec | None = None,
        output_strategy: OutputStrategy | str | None = None,
        cache: ModelCacheControl | None = None,
        dynamic_loading: DynamicToolLoadingSpec | None = None,
        tool_routing_plan: ResolvedToolPlan | None = None,
        data_sources: list[DataSourceRef] | None = None,
        context_flags: list[str] | None = None,
        tool_session: ToolSession | None = None,
    ) -> ResolvedRunSpec:
        """Assemble the resolved run plan used for prompt composition."""

        registry = tool_session.registry if tool_session is not None else self.tools.registry
        available_tool_ids = [tool.name for tool in registry.all_tools()]
        for tool_name in _tool_names(tool_definitions):
            if tool_name not in available_tool_ids:
                available_tool_ids.append(tool_name)
        return ResolvedRunSpec(
            provider=provider_ref.provider_key,
            model=model_name,
            provider_profile=capability_profile,
            input=input,
            base_instructions=instructions,
            channel=prompt_spec.channel,
            tools=_resolved_tools(tool_definitions, registry=registry),
            hosted_tools=resolved_hosted_tools(effective_hosted_tools),
            mcp_toolsets=mcp_toolsets,
            workspace=workspace,
            output_spec=output_spec,
            output_strategy=output_strategy,
            dynamic_loading=dynamic_loading,
            tool_routing_plan=tool_routing_plan,
            cache=cache,
            data_sources=list(data_sources or []),
            context_flags=list(context_flags or []),
            available_tool_ids=available_tool_ids,
            available_prompt_fragments=[
                fragment
                for tool in registry.all_tools()
                for fragment in tool.prompt_fragments
            ],
            metadata={
                "prompt_mode": prompt_spec.mode,
                "prompt_parity": prompt_spec.parity,
            },
        )

    def _tool_payload(
        self, names: list[str] | None, *, tool_session: ToolSession | None = None
    ) -> list[dict[str, Any]]:
        if not names:
            return []
        provider_tools = (
            tool_session.to_provider_tools() if tool_session is not None else self.tools.to_provider_tools()
        )
        wanted = set(names)
        return [t for t in provider_tools if t["name"] in wanted]

    def _tool_runtime_for(
        self,
        context: dict[str, Any] | None,
        *,
        tool_session: ToolSession | None = None,
        max_concurrent: int | None = None,
        timeout: float | None = None,
        allowed_tools: set[str] | None = None,
    ) -> ToolRuntime:
        if tool_session is not None:
            return tool_session.runtime(
                context=context,
                max_concurrent=max_concurrent,
                timeout=timeout,
                allowed_tools=allowed_tools,
            )
        if (
            context is None
            and max_concurrent is None
            and timeout is None
            and allowed_tools is None
        ):
            return self.tools.default_runtime
        return ToolRuntime(
            self.tools.registry,
            context=context or {},
            max_concurrent=max_concurrent,
            timeout=timeout,
            allowed_tools=allowed_tools,
        )

    def _split_toolsets(self, toolsets: list[Any] | None) -> tuple[list[MCPToolset], list[Any]]:
        mcp_toolsets: list[MCPToolset] = []
        local_toolsets: list[Any] = []
        for toolset in toolsets or []:
            if isinstance(toolset, MCPToolset):
                mcp_toolsets.append(toolset)
            else:
                local_toolsets.append(toolset)
        return mcp_toolsets, local_toolsets

    def _register_local_toolsets(
        self,
        tool_session: ToolSession,
        toolsets: list[Any],
    ) -> list[str]:
        names: list[str] = []
        for toolset in toolsets:
            tools = _toolset_definitions(toolset)
            for definition in tools:
                tool_session.registry.add(definition)
                names.append(definition.name)
        return names

    def _should_use_dynamic_tools(
        self,
        *,
        selection: ToolSelection,
        tool_session: ToolSession | None,
        budget: ToolBudget,
    ) -> bool:
        if selection == "dynamic":
            return True
        if selection == "static":
            return False
        registry = tool_session.registry if tool_session is not None else self.tools.registry
        return len(registry.all_tools()) > budget.large_catalog_threshold

    async def _dynamic_tool_session(
        self,
        *,
        tool_session: ToolSession,
        initial_visible: list[str],
        budget: ToolBudget,
        policy: Policy | None,
    ) -> DynamicToolsetSession:
        allowed_tools: list[ToolDefinition] = []
        rejected: list[dict[str, Any]] = []
        for definition in tool_session.registry.all_tools():
            if definition.metadata.get("agent_runtime_dynamic_meta") is True:
                continue
            decision = await _tool_exposure_decision(definition, policy)
            if decision.verdict == "allow":
                allowed_tools.append(definition)
            else:
                rejected.append(
                    {
                        "name": definition.name,
                        "reason": decision.reason or decision.verdict,
                        "checkpoint": "before_tool_exposure",
                    }
                )
        session = DynamicToolsetSession(
            registry=tool_session.registry,
            catalog=ToolCatalog(allowed_tools),
            budget=budget,
            rejected_tools=rejected,
        )
        session.install_meta_tools()
        allowed_names = {tool.name for tool in allowed_tools}
        for name in initial_visible:
            if name not in allowed_names:
                continue
            if (
                budget.max_tools_visible is not None
                and len(session.visible_names) >= budget.max_tools_visible
            ):
                session.rejected_tools.append(
                    {
                        "name": name,
                        "reason": "max_tools_visible_exceeded",
                        "limit": budget.max_tools_visible,
                    }
                )
                continue
            session.visible_names.add(name)
            session.core_names.add(name)
        return session

    async def _filter_tools_for_exposure(
        self,
        names: list[str],
        *,
        tool_session: ToolSession | None,
        policy: Policy | None,
        budget: ToolBudget | None = None,
    ) -> tuple[list[str], list[AgentEvent]]:
        """Apply exposure policy, de-duplication, and budget limits to requested tools."""

        if not names:
            return [], []
        registry = tool_session.registry if tool_session is not None else self.tools.registry
        allowed: list[str] = []
        events: list[AgentEvent] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            try:
                definition = registry.get(name)
            except ToolExecutionError:
                events.append(
                    AgentEvent(
                        type=EventTypes.TOOL_CHOICE_REJECTED,
                        data={"name": name, "reason": "unknown_tool"},
                    )
                )
                continue
            decision = await _tool_exposure_decision(definition, policy)
            if decision.verdict == "allow":
                if (
                    budget is not None
                    and budget.max_tools_visible is not None
                    and len(allowed) >= budget.max_tools_visible
                ):
                    events.append(
                        AgentEvent(
                            type=EventTypes.TOOL_CHOICE_REJECTED,
                            data={
                                "name": name,
                                "reason": "max_tools_visible_exceeded",
                                "limit": budget.max_tools_visible,
                            },
                        )
                    )
                    continue
                allowed.append(name)
                events.append(
                    AgentEvent(
                        type=EventTypes.TOOL_CHOICE_SELECTED,
                        data={"name": name, "reason": "initially_visible", "dynamic": False},
                    )
                )
                continue
            events.append(
                AgentEvent(
                    type=EventTypes.TOOL_CHOICE_REJECTED,
                    data={
                        "name": name,
                        "reason": decision.reason or decision.verdict,
                        "checkpoint": "before_tool_exposure",
                    },
                )
            )
        return allowed, events

    async def _open_runtime_workspace(
        self,
        workspace: Any,
        *,
        provider: Any | None,
        policy: Any,
    ) -> tuple[Any, Any, bool]:
        workspace_ref, workspace_provider, opened = await self.workspaces.resolve(
            workspace,
            provider=provider,
            policy=policy,
        )
        if workspace_ref is None or workspace_provider is None:
            raise ConfigurationError("workspace could not be resolved.")
        return workspace_provider, workspace_ref, opened


def _toolset_definitions(toolset: Any) -> list[ToolDefinition]:
    if isinstance(toolset, Toolset):
        return toolset.all_tools()
    all_tools = getattr(toolset, "all_tools", None)
    if callable(all_tools):
        result = all_tools()
        if isinstance(result, list):
            return [tool for tool in result if isinstance(tool, ToolDefinition)]
    tools = getattr(toolset, "tools", None)
    if callable(tools):
        result = tools()
        if isinstance(result, list):
            return [tool for tool in result if isinstance(tool, ToolDefinition)]
    if isinstance(tools, list):
        return [tool for tool in tools if isinstance(tool, ToolDefinition)]
    raise ConfigurationError(
        "toolsets entries must be MCPToolset instances or expose all_tools()."
    )


def _normalize_tools_argument(
    tools: list[str] | str | None,
    *,
    tool_routing: ToolRoutingSpec | None,
) -> tuple[list[str], ToolRoutingSpec | None]:
    if tools == "dynamic":
        return [], tool_routing or ToolRoutingSpec.coding_defaults()
    if isinstance(tools, str):
        return [tools], tool_routing
    return list(tools or []), tool_routing


def _routing_enabled(tool_routing: ToolRoutingSpec | None) -> bool:
    return tool_routing is not None and tool_routing.mode not in {"disabled", "explicit"}


def _routing_input_text(value: str | list[Any]) -> str:
    if isinstance(value, str):
        return value
    parts: list[str] = []
    for item in value:
        if isinstance(item, RunItem):
            content = item.data.get("content") or item.data.get("error")
            if content is not None:
                parts.append(str(content))
            continue
        parts.append(str(item))
    return "\n".join(parts)


def _workspace_summary(workspace: Any | None) -> dict[str, Any]:
    if workspace is None:
        return {}
    return {
        "id": getattr(workspace, "id", None),
        "kind": getattr(workspace, "kind", None),
        "provider": getattr(workspace, "provider", None),
        "root": getattr(workspace, "root", None),
    }


def _find_candidate(
    candidates: list[ToolCandidate],
    name_or_ref: str,
) -> ToolCandidate | None:
    for candidate in candidates:
        if candidate.name == name_or_ref or candidate.ref == name_or_ref:
            return candidate
    return None


async def _tool_exposure_decision(
    definition: ToolDefinition,
    policy: Policy | None,
) -> PolicyDecision:
    if policy is None:
        return PolicyDecision.allow()
    return await policy.check(
        PolicyRequest(
            checkpoint="before_tool_exposure",
            action=definition.name,
            metadata={
                "category": definition.category,
                "tags": list(definition.tags),
                "risk": definition.risk,
                "scopes": list(definition.scopes),
                "latency": definition.latency,
                "cost": definition.cost,
                "side_effects": list(definition.side_effects),
                "tool_metadata": dict(definition.metadata),
            },
        )
    )
