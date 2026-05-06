from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4

from blackbox.core.accounting import (
    ModelCatalog,
    add_usage,
    usage_provider_details,
    usage_to_dict,
)
from blackbox.core.artifacts import Artifact
from blackbox.core.cache import InMemoryProviderCacheStore, ProviderCacheStore
from blackbox.core.capabilities import ModelCapabilityProfile, get_model_capability_profile
from blackbox.core.errors import ConfigurationError
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.results import OutputSpec, OutputStrategy
from blackbox.core.state import ProviderState
from blackbox.observability.presets import ObservabilityPreset
from blackbox.observability.traces import TraceContext, trace_metadata_from_events
from blackbox.output.schema import OutputSchema, build_output_schema
from blackbox.providers.base import (
    CompactionControl,
    ModelCacheControl,
    ModelRequestControls,
    ToolSearchControl,
    TurnRequest,
    TurnResult,
)
from blackbox.providers.model_adapters.capability_validation import (
    validate_turn_request_capabilities,
)
from blackbox.providers.registry import ProviderRef, ProviderRegistry
from blackbox.runtime.config import RuntimeConfig
from blackbox.runtime.event_metadata import (
    _attach_accounting_metadata,
    _event_usage,
    _hosted_tool_metadata_from_events,
    _mcp_metadata_from_events,
    _pricing_metadata,
    _prompt_metadata_from_events,
    _provider_cache_metadata,
    _tool_usage_from_events,
)
from blackbox.runtime.workspace_results import _workspace_metadata_from_events
from blackbox.tools.hosted.specs import HostedToolHandlers, HostedToolSpec


@dataclass(slots=True)
class ModelRuntime:
    """Facade for direct model turns with tracing, tools, and result aggregation."""

    registry: ProviderRegistry
    model_catalog: ModelCatalog = field(default_factory=ModelCatalog)
    provider_cache_store: ProviderCacheStore = field(
        default_factory=InMemoryProviderCacheStore
    )
    observability: ObservabilityPreset = field(
        default_factory=ObservabilityPreset.disabled
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
        provider: str | None = None,
        model: str | None = None,
        input: str | Sequence[object],
        config: RuntimeConfig | Mapping[str, Any] | None = None,
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
        emit_observability: bool = True,
        **kwargs: object,
    ) -> AsyncIterator[AgentEvent]:
        """Validate and stream a single model turn as traced runtime events."""

        if isinstance(config, RuntimeConfig):
            config_values = config.to_kwargs(surface="model")
        else:
            config_values = {}
            if config is not None:
                kwargs = {**dict(kwargs), "config": config}
        provider = cast(
            str | None,
            _consume_config_value(config_values, "provider", provider),
        )
        model = cast(str | None, _consume_config_value(config_values, "model", model))
        provider_state = cast(
            ProviderState | None,
            _consume_config_value(config_values, "provider_state", provider_state),
        )
        tools = cast(list[Any] | None, _consume_config_value(config_values, "tools", tools))
        hosted_tools = cast(
            list[HostedToolSpec] | None,
            _consume_config_value(config_values, "hosted_tools", hosted_tools),
        )
        output_schema = _coerce_output_schema(
            _consume_config_value(config_values, "output_schema", output_schema)
        )
        output_spec = _coerce_output_spec(config_values.pop("output_spec", None))
        if output_schema is None and output_spec is not None:
            output_schema = build_output_schema(output_spec)
            if output_strategy is None:
                output_strategy = output_spec.strategy
        output_strategy = cast(
            OutputStrategy | None,
            _consume_config_value(config_values, "output_strategy", output_strategy),
        )
        instructions = cast(
            str | None,
            _consume_config_value(config_values, "instructions", instructions),
        )
        temperature = cast(
            float | None,
            _consume_config_value(config_values, "temperature", temperature),
        )
        top_p = cast(float | None, _consume_config_value(config_values, "top_p", top_p))
        max_output_tokens = cast(
            int | None,
            _consume_config_value(config_values, "max_output_tokens", max_output_tokens),
        )
        tool_choice = _consume_config_value(config_values, "tool_choice", tool_choice)
        parallel_tool_calls = cast(
            bool | None,
            _consume_config_value(
                config_values, "parallel_tool_calls", parallel_tool_calls
            ),
        )
        reasoning_effort = cast(
            str | None,
            _consume_config_value(config_values, "reasoning_effort", reasoning_effort),
        )
        cache = _coerce_cache(_consume_config_value(config_values, "cache", cache))
        verbosity = cast(
            str | None,
            _consume_config_value(config_values, "verbosity", verbosity),
        )
        reasoning_summary = cast(
            str | None,
            _consume_config_value(
                config_values, "reasoning_summary", reasoning_summary
            ),
        )
        state_mode = cast(
            str | None,
            _consume_config_value(config_values, "state_mode", state_mode),
        )
        background = cast(
            bool | None,
            _consume_config_value(config_values, "background", background),
        )
        store = cast(bool | None, _consume_config_value(config_values, "store", store))
        include = _coerce_str_list(
            _consume_config_value(config_values, "include", include)
        )
        tool_search = _coerce_tool_search(
            _consume_config_value(config_values, "tool_search", tool_search)
        )
        compaction = _coerce_compaction(
            _consume_config_value(config_values, "compaction", compaction)
        )
        modalities = _coerce_str_list(
            _consume_config_value(config_values, "modalities", modalities)
        )
        run_id = cast(str | None, _consume_config_value(config_values, "run_id", run_id))
        trace_id = cast(
            str | None,
            _consume_config_value(config_values, "trace_id", trace_id),
        )
        parent_span_id = cast(
            str | None,
            _consume_config_value(config_values, "parent_span_id", parent_span_id),
        )
        if provider is None:
            raise ValueError("provider must be provided explicitly or through config.")
        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        request = TurnRequest(
            model=model_name,
            input=input if isinstance(input, str) else list(input),
            provider_state=provider_state,
            tools=_list_or_empty(tools),
            hosted_tools=cast(list[HostedToolSpec], _list_or_empty(hosted_tools)),
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
            extra={**config_values, **dict(kwargs)},
        )
        validate_turn_request_capabilities(provider=adapter, request=request)
        effective_run_id = run_id or f"run_{uuid4().hex}"
        trace_context = TraceContext.for_run(
            trace_id or effective_run_id,
            root_span_id=parent_span_id,
        )
        sequence = 0
        async for event in adapter.stream_turn(request):
            stamped = trace_context.stamp(
                event,
                run_id=effective_run_id,
                sequence=sequence,
            )
            if emit_observability:
                await self.observability.emit_event(stamped)
            yield stamped
            sequence += 1

    async def run(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        input: str | Sequence[object],
        config: RuntimeConfig | Mapping[str, Any] | None = None,
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
        emit_observability: bool = True,
        **kwargs: object,
    ) -> TurnResult:
        """Execute a model turn and collect text, artifacts, state, usage, and metadata."""

        effective_cache = cache
        if effective_cache is None and isinstance(config, RuntimeConfig):
            effective_cache = _coerce_cache(config.to_kwargs(surface="model").get("cache"))
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
            config=config,
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
            emit_observability=emit_observability,
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
        metadata.update(
            _pricing_metadata(
                model_catalog=self.model_catalog,
                provider=completed_provider,
                model=completed_model,
                usage=usage,
            )
        )
        cache_metadata = await _provider_cache_metadata(
            provider=completed_provider,
            model=completed_model,
            cache=effective_cache,
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
        if emit_observability:
            self.observability.export_run(events, metadata=metadata)
        return TurnResult(
            text="".join(text_parts),
            events=events,
            provider_state=captured_state,
            artifacts=artifacts,
            metadata=metadata,
        )


def _consume_config_value(
    values: dict[str, Any],
    name: str,
    explicit: Any,
) -> Any:
    configured = values.pop(name, None)
    return explicit if explicit is not None else configured


def _list_or_empty(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _coerce_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    raise ConfigurationError(f"Expected a string list, got {type(value)!r}.")


def _coerce_cache(value: Any) -> ModelCacheControl | None:
    if value is None or isinstance(value, ModelCacheControl):
        return value
    if isinstance(value, str):
        return ModelCacheControl(strategy=cast(Any, value))
    if isinstance(value, Mapping):
        return ModelCacheControl(**dict(value))
    raise ConfigurationError(f"Invalid cache config: {value!r}.")


def _coerce_tool_search(value: Any) -> ToolSearchControl | None:
    if value is None or isinstance(value, ToolSearchControl):
        return value
    if isinstance(value, bool):
        return ToolSearchControl(enabled=value)
    if isinstance(value, Mapping):
        return ToolSearchControl(**dict(value))
    raise ConfigurationError(f"Invalid tool_search config: {value!r}.")


def _coerce_compaction(value: Any) -> CompactionControl | None:
    if value is None or isinstance(value, CompactionControl):
        return value
    if isinstance(value, str):
        return CompactionControl(strategy=cast(Any, value))
    if isinstance(value, Mapping):
        return CompactionControl(**dict(value))
    raise ConfigurationError(f"Invalid compaction config: {value!r}.")


def _coerce_output_spec(value: Any) -> OutputSpec | None:
    if value is None or isinstance(value, OutputSpec):
        return value
    if isinstance(value, Mapping):
        return OutputSpec(**dict(value))
    raise ConfigurationError(f"Invalid output_spec config: {value!r}.")


def _coerce_output_schema(value: Any) -> OutputSchema | None:
    if value is None or isinstance(value, OutputSchema):
        return value
    if isinstance(value, Mapping):
        payload = dict(value)
        raw_schema = payload.get("schema")
        if isinstance(raw_schema, Mapping):
            return OutputSchema(
                name=str(payload.get("name") or "output"),
                schema=dict(raw_schema),
                description=(
                    str(payload["description"])
                    if payload.get("description") is not None
                    else None
                ),
                strict=bool(payload.get("strict", True)),
                target_type=dict(raw_schema),
            )
        return OutputSchema(
            name="output",
            schema=payload,
            target_type=payload,
        )
    raise ConfigurationError(f"Invalid output_schema config: {value!r}.")
