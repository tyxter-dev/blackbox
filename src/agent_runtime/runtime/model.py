from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4

from agent_runtime.core.accounting import (
    ModelCatalog,
    add_usage,
    usage_provider_details,
    usage_to_dict,
)
from agent_runtime.core.artifacts import Artifact
from agent_runtime.core.cache import InMemoryProviderCacheStore, ProviderCacheStore
from agent_runtime.core.capabilities import ModelCapabilityProfile, get_model_capability_profile
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.results import OutputStrategy
from agent_runtime.core.state import ProviderState
from agent_runtime.observability.traces import TraceContext, trace_metadata_from_events
from agent_runtime.output.schema import OutputSchema
from agent_runtime.providers.base import (
    CompactionControl,
    ModelCacheControl,
    ModelRequestControls,
    ToolSearchControl,
    TurnRequest,
    TurnResult,
)
from agent_runtime.providers.model_adapters.capability_validation import (
    validate_turn_request_capabilities,
)
from agent_runtime.providers.registry import ProviderRef, ProviderRegistry
from agent_runtime.runtime._helpers import (
    _attach_accounting_metadata,
    _event_usage,
    _hosted_tool_metadata_from_events,
    _mcp_metadata_from_events,
    _prompt_metadata_from_events,
    _provider_cache_metadata,
    _tool_usage_from_events,
    _workspace_metadata_from_events,
)
from agent_runtime.tools.hosted.specs import HostedToolHandlers, HostedToolSpec


@dataclass(slots=True)
class ModelRuntime:
    registry: ProviderRegistry
    model_catalog: ModelCatalog = field(default_factory=ModelCatalog)
    provider_cache_store: ProviderCacheStore = field(
        default_factory=InMemoryProviderCacheStore
    )

    def capabilities(
        self,
        provider: str,
        *,
        model: str | None = None,
    ) -> ModelCapabilityProfile:
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        adapter = self.registry.get_model(provider_ref.provider_key)
        return get_model_capability_profile(adapter, model_name)

    async def stream(
        self,
        *,
        provider: str,
        model: str | None = None,
        input: str | Sequence[object],
        provider_state: ProviderState | None = None,
        tools: list[Any] | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        hosted_tool_handlers: HostedToolHandlers | None = None,
        output_schema: OutputSchema | None = None,
        output_strategy: OutputStrategy | None = None,
        instructions: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        tool_choice: Any | None = None,
        parallel_tool_calls: bool | None = None,
        reasoning_effort: str | None = None,
        cache: ModelCacheControl | None = None,
        verbosity: str | None = None,
        reasoning_summary: str | None = None,
        state_mode: str | None = None,
        background: bool | None = None,
        store: bool | None = None,
        include: list[str] | None = None,
        tool_search: ToolSearchControl | None = None,
        compaction: CompactionControl | None = None,
        modalities: list[str] | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        **kwargs: object,
    ) -> AsyncIterator[AgentEvent]:
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        request = TurnRequest(
            model=model_name,
            input=input if isinstance(input, str) else list(input),
            provider_state=provider_state,
            tools=list(tools or []),
            hosted_tools=list(hosted_tools or []),
            output_schema=output_schema,
            output_strategy=output_strategy,
            controls=ModelRequestControls(
                instructions=instructions,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
                tool_choice=tool_choice,
                parallel_tool_calls=parallel_tool_calls,
                reasoning_effort=reasoning_effort,
                cache=cache,
                verbosity=verbosity,
                reasoning_summary=reasoning_summary,
                state_mode=cast(Any, state_mode),
                background=background,
                store=store,
                include=include,
                tool_search=tool_search,
                compaction=compaction,
                modalities=modalities,
            ),
            extra=dict(kwargs),
        )
        validate_turn_request_capabilities(provider=adapter, request=request)
        effective_run_id = run_id or f"run_{uuid4().hex}"
        trace_context = TraceContext.for_run(
            trace_id or effective_run_id,
            root_span_id=parent_span_id,
        )
        sequence = 0
        async for event in adapter.stream_turn(request):
            yield trace_context.stamp(
                event,
                run_id=effective_run_id,
                sequence=sequence,
            )
            sequence += 1

    async def run(
        self,
        *,
        provider: str,
        model: str | None = None,
        input: str | Sequence[object],
        provider_state: ProviderState | None = None,
        tools: list[Any] | None = None,
        hosted_tools: list[HostedToolSpec] | None = None,
        hosted_tool_handlers: HostedToolHandlers | None = None,
        output_schema: OutputSchema | None = None,
        output_strategy: OutputStrategy | None = None,
        instructions: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
        tool_choice: Any | None = None,
        parallel_tool_calls: bool | None = None,
        reasoning_effort: str | None = None,
        cache: ModelCacheControl | None = None,
        verbosity: str | None = None,
        reasoning_summary: str | None = None,
        state_mode: str | None = None,
        background: bool | None = None,
        store: bool | None = None,
        include: list[str] | None = None,
        tool_search: ToolSearchControl | None = None,
        compaction: CompactionControl | None = None,
        modalities: list[str] | None = None,
        run_id: str | None = None,
        **kwargs: object,
    ) -> TurnResult:
        events: list[AgentEvent] = []
        text_parts: list[str] = []
        artifacts: list[Artifact] = []
        captured_state: ProviderState | None = None
        usage = None
        completed_provider: str | None = None
        completed_model: str | None = None
        async for event in self.stream(
            provider=provider,
            model=model,
            input=input,
            provider_state=provider_state,
            tools=tools,
            hosted_tools=hosted_tools,
            hosted_tool_handlers=hosted_tool_handlers,
            output_schema=output_schema,
            output_strategy=output_strategy,
            instructions=instructions,
            temperature=temperature,
            top_p=top_p,
            max_output_tokens=max_output_tokens,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            reasoning_effort=reasoning_effort,
            cache=cache,
            verbosity=verbosity,
            reasoning_summary=reasoning_summary,
            state_mode=state_mode,
            background=background,
            store=store,
            include=include,
            tool_search=tool_search,
            compaction=compaction,
            modalities=modalities,
            run_id=run_id,
            **cast(Any, kwargs),
        ):
            events.append(event)
            if event.type == EventTypes.MODEL_TEXT_DELTA:
                delta = event.data.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            artifact = event.data.get("artifact")
            if isinstance(artifact, Artifact):
                artifacts.append(artifact)
            maybe_state = event.data.get("provider_state")
            if isinstance(maybe_state, ProviderState):
                captured_state = maybe_state
            if event.type == EventTypes.MODEL_COMPLETED:
                usage = add_usage(usage, _event_usage(event))
                completed_provider = event.provider or completed_provider
                model_value = event.data.get("model")
                if isinstance(model_value, str):
                    completed_model = model_value
        usage = add_usage(usage, _tool_usage_from_events(events))
        metadata: dict[str, Any] = {}
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
        hosted_metadata = _hosted_tool_metadata_from_events(events)
        if hosted_metadata:
            metadata["hosted_tools"] = hosted_metadata
        workspace_metadata = _workspace_metadata_from_events(events)
        if workspace_metadata:
            metadata["workspaces"] = workspace_metadata
        prompt_metadata = _prompt_metadata_from_events(events)
        if prompt_metadata:
            metadata["prompt"] = prompt_metadata
        trace_metadata = trace_metadata_from_events(events, metadata=metadata)
        if trace_metadata["spans"]:
            metadata["trace"] = trace_metadata
        _attach_accounting_metadata(metadata)
        return TurnResult(
            text="".join(text_parts),
            events=events,
            provider_state=captured_state,
            artifacts=artifacts,
            metadata=metadata,
        )
