from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from agent_runtime.core.accounting import usage_from_openai_response
from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.errors import (
    ProviderExecutionError,
    ProviderNotConfiguredError,
    UnsupportedFeatureError,
)
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemStatus, ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.hosted_tools import openai_include_values, to_openai_tool
from agent_runtime.models._support import (
    is_retryable_status_error,
    sleep_for_retry,
    strip_private_fields,
)
from agent_runtime.providers.base import TurnRequest

_TYPED_ITEM_EVENTS = {
    "message": EventTypes.MODEL_ITEM_CREATED,
    "reasoning": EventTypes.MODEL_ITEM_CREATED,
    "tool_search_call": EventTypes.TOOL_SEARCH_REQUESTED,
    "tool_search_result": EventTypes.TOOL_SEARCH_COMPLETED,
    "mcp_list_tools": EventTypes.MCP_LIST_TOOLS_COMPLETED,
    "mcp_call": EventTypes.MCP_CALL_STARTED,
    "mcp_approval_request": EventTypes.MCP_APPROVAL_REQUIRED,
}

_ITEM_TYPE_TO_RUN_ITEM = {
    "message": ItemTypes.MESSAGE,
    "reasoning": ItemTypes.REASONING,
    "function_call": ItemTypes.FUNCTION_CALL,
    "tool_search_call": ItemTypes.TOOL_SEARCH_CALL,
    "tool_search_result": ItemTypes.TOOL_SEARCH_OUTPUT,
    "mcp_list_tools": ItemTypes.MCP_LIST_TOOLS,
    "mcp_call": ItemTypes.MCP_CALL,
    "mcp_approval_request": ItemTypes.MCP_APPROVAL_REQUEST,
}

_HOSTED_TOOL_CALL_TYPES = {
    "web_search_call": "web_search",
    "file_search_call": "file_search",
    "code_interpreter_call": "code_interpreter",
    "computer_call": "computer",
    "image_generation_call": "image_generation",
}

_HOSTED_TOOL_DATA_KEYS = (
    "status",
    "query",
    "results",
    "output",
    "code",
    "action",
    "call_id",
)

_MCP_ITEM_DATA_KEYS = (
    "server_label",
    "tools",
    "name",
    "arguments",
    "output",
    "error",
    "approval_request_id",
)


class OpenAIResponsesProvider:
    """Provider-native adapter for the OpenAI Responses API.

    Maps native streaming events to AgentEvents while preserving raw payloads.
    Stable item types get typed events; known hosted tools become hosted-tool run
    items. Unknown provider items fall back to MODEL_ITEM_CREATED with the
    original item type stashed in ``data['item_type']``.
    """

    provider_id = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int = 2,
        retry_min_delay: float = 0.5,
    ) -> None:
        self.api_key = api_key
        self._client = client
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_min_delay = retry_min_delay
        self._owns_client = client is None

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            supports_streaming_events=True,
            supports_function_tools=True,
            supports_parallel_tool_calls=True,
            supports_hosted_tools=True,
            supports_tool_search=True,
            supports_client_executed_tool_search=True,
            supports_remote_mcp=True,
            supports_reasoning_items=True,
            supports_provider_state=True,
            supports_structured_output=True,
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ProviderNotConfiguredError(
                "OpenAIResponsesProvider requires either a client, an api_key, "
                "or OPENAI_API_KEY."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "OpenAIResponsesProvider needs the 'openai' package; install with the "
                "[openai] extra."
            ) from exc
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if self.base_url is not None:
            client_kwargs["base_url"] = self.base_url
        if self.timeout is not None:
            client_kwargs["timeout"] = self.timeout
        self._client = AsyncOpenAI(**client_kwargs)
        return self._client

    async def close(self) -> None:
        if self._client is None or not self._owns_client:
            return
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result
        self._client = None

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        client = self._get_client()
        kwargs = self._build_request_kwargs(request)

        yield AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            provider=self.provider_id,
            data={"model": request.model},
        )

        attempt = 0
        while True:
            emitted_provider_output = False
            try:
                async with client.responses.stream(**kwargs) as stream:
                    async for sdk_event in stream:
                        mapped = _map_event(sdk_event, provider=self.provider_id)
                        if mapped is not None:
                            emitted_provider_output = True
                            yield mapped
                    final_response = await _get_final_response(stream)
                break
            except ProviderExecutionError:
                raise
            except Exception as exc:
                if (
                    emitted_provider_output
                    or attempt >= self.max_retries
                    or not self._is_retryable_error(exc)
                ):
                    raise ProviderExecutionError(
                        f"OpenAI Responses stream failed: {exc!s}"
                    ) from exc
                await sleep_for_retry(
                    exc, attempt=attempt, base_delay=self.retry_min_delay
                )
                attempt += 1

        provider_state = _build_provider_state(final_response, provider=self.provider_id)
        usage = usage_from_openai_response(final_response)
        data: dict[str, Any] = {"model": request.model, "provider_state": provider_state}
        if usage is not None:
            data["usage"] = usage.to_dict()
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider=self.provider_id,
            data=data,
            raw=final_response,
        )

    def _is_retryable_error(self, error: Exception) -> bool:
        try:
            from openai import APIConnectionError, APIStatusError, APITimeoutError
        except ImportError:
            return is_retryable_status_error(error)
        if isinstance(error, (APIConnectionError, APITimeoutError)):
            return True
        if isinstance(error, APIStatusError):
            body = getattr(error, "body", None)
            if (
                error.status_code == 429
                and isinstance(body, dict)
                and isinstance(body.get("error"), dict)
                and body["error"].get("code") == "insufficient_quota"
            ):
                return False
        return is_retryable_status_error(error)

    @staticmethod
    def _build_request_kwargs(request: TurnRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "input": _coerce_input(request.input),
        }
        tools = [*request.tools, *(to_openai_tool(tool) for tool in request.hosted_tools)]
        if tools:
            kwargs["tools"] = tools
        include_values = openai_include_values(request.hosted_tools)
        if include_values:
            kwargs["include"] = include_values
        controls = request.controls
        if controls.instructions is not None:
            kwargs["instructions"] = controls.instructions
        if controls.temperature is not None:
            kwargs["temperature"] = controls.temperature
        if controls.top_p is not None:
            kwargs["top_p"] = controls.top_p
        if controls.max_output_tokens is not None:
            kwargs["max_output_tokens"] = controls.max_output_tokens
        if controls.tool_choice is not None:
            kwargs["tool_choice"] = controls.tool_choice
        if controls.parallel_tool_calls is not None:
            kwargs["parallel_tool_calls"] = controls.parallel_tool_calls
        if controls.reasoning_effort is not None:
            kwargs["reasoning"] = {"effort": controls.reasoning_effort}
        if controls.cache is not None:
            if controls.cache.strategy == "bypass":
                raise UnsupportedFeatureError(
                    "OpenAI Responses prompt caching is provider-managed and cannot be bypassed "
                    "through ModelCacheControl."
                )
            if controls.cache.key is not None:
                kwargs["prompt_cache_key"] = controls.cache.key
            if controls.cache.ttl is not None:
                kwargs["prompt_cache_retention"] = controls.cache.ttl
            kwargs.update(controls.cache.extra)
        if request.output_schema is not None and request.output_strategy == "provider_native":
            _merge_text_format(kwargs, request)
        if request.provider_state:
            previous = (
                request.provider_state.previous_response_id
                or request.provider_state.continuation.get("previous_response_id")
            )
            if previous:
                kwargs["previous_response_id"] = previous
        extra = dict(request.extra)
        if request.output_schema is not None and request.output_strategy == "provider_native":
            extra_text = extra.pop("text", None)
            if extra_text is not None:
                if not isinstance(extra_text, dict):
                    raise ValueError("extra['text'] must be a dict when using provider_native output.")
                if "format" in extra_text and extra_text["format"] != kwargs["text"]["format"]:
                    raise ValueError(
                        "extra['text']['format'] conflicts with provider_native output schema."
                    )
                kwargs["text"] = {**extra_text, "format": kwargs["text"]["format"]}
        kwargs.update(extra)
        return kwargs


def _coerce_input(value: str | list[Any]) -> Any:
    if isinstance(value, str):
        return value
    converted: list[Any] = []
    for entry in value:
        if isinstance(entry, RunItem) and entry.type == ItemTypes.FUNCTION_RESULT:
            converted.append(
                {
                    "type": "function_call_output",
                    "call_id": entry.data.get("call_id", ""),
                    "output": entry.data.get("content", entry.data.get("error", "")),
                }
            )
        else:
            converted.append(strip_private_fields(entry))
    return converted


def _merge_text_format(kwargs: dict[str, Any], request: TurnRequest) -> None:
    assert request.output_schema is not None
    response_format: dict[str, Any] = {
        "type": "json_schema",
        "name": request.output_schema.name,
        "schema": request.output_schema.schema,
        "strict": request.output_schema.strict,
    }
    if request.output_schema.description is not None:
        response_format["description"] = request.output_schema.description
    text_config = dict(kwargs.get("text", {}))
    text_config["format"] = response_format
    kwargs["text"] = text_config


def _map_event(sdk_event: Any, *, provider: str) -> AgentEvent | None:
    event_type = getattr(sdk_event, "type", None)
    if not isinstance(event_type, str):
        return None

    if event_type == "response.output_text.delta":
        return AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA,
            provider=provider,
            item_id=getattr(sdk_event, "item_id", None),
            data={"delta": getattr(sdk_event, "delta", "")},
            raw=sdk_event,
        )

    if event_type == "response.reasoning_summary_text.delta":
        return AgentEvent(
            type=EventTypes.MODEL_REASONING_DELTA,
            provider=provider,
            item_id=getattr(sdk_event, "item_id", None),
            data={"delta": getattr(sdk_event, "delta", "")},
            raw=sdk_event,
        )

    if event_type == "response.output_item.added":
        item = getattr(sdk_event, "item", None)
        return _map_item_added(item, provider=provider, raw=sdk_event)

    if event_type == "response.output_item.done":
        item = getattr(sdk_event, "item", None)
        return _map_item_done(item, provider=provider, raw=sdk_event)

    if event_type in {"response.created", "response.in_progress"}:
        return None

    if event_type == "response.completed":
        return None  # handled via final response

    return AgentEvent(
        type=EventTypes.MODEL_ITEM_CREATED,
        provider=provider,
        data={"item_type": event_type, "passthrough": True},
        raw=sdk_event,
    )


def _map_item_added(item: Any, *, provider: str, raw: Any) -> AgentEvent | None:
    if item is None:
        return None
    item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
    if not item_type:
        return None

    canonical_event = _TYPED_ITEM_EVENTS.get(item_type, EventTypes.MODEL_ITEM_CREATED)
    run_item_type = _ITEM_TYPE_TO_RUN_ITEM.get(item_type, item_type)
    item_id = getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else None)

    data: dict[str, Any] = {"item_type": item_type}
    status: ItemStatus = "created"
    if item_type == "function_call":
        data["call_id"] = _attr(item, "call_id") or item_id or ""
        data["name"] = _attr(item, "name") or ""
        data["arguments"] = _parse_arguments(_attr(item, "arguments"))
    elif item_type in _ITEM_TYPE_TO_RUN_ITEM and item_type.startswith("mcp_"):
        data.update(_mcp_item_data(item, item_type=item_type))
        if item_type == "mcp_approval_request":
            status = "requires_action"
    elif item_type in _HOSTED_TOOL_CALL_TYPES:
        run_item_type = ItemTypes.HOSTED_TOOL_CALL
        data.update(_hosted_tool_data(item, item_type=item_type))

    run_item = RunItem(
        type=run_item_type,
        provider=provider,
        status=status,
        id=item_id or f"item_{id(item)}",
        data=dict(data),
        raw=item,
    )
    data["item"] = run_item
    return AgentEvent(
        type=canonical_event,
        provider=provider,
        item_id=run_item.id,
        data=data,
        raw=raw,
    )


def _map_item_done(item: Any, *, provider: str, raw: Any) -> AgentEvent | None:
    if item is None:
        return None
    item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
    if not item_type:
        return None

    item_id = getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else None)

    if item_type == "function_call":
        return AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider=provider,
            item_id=item_id,
            data={
                "call_id": _attr(item, "call_id") or item_id or "",
                "name": _attr(item, "name") or "",
                "arguments": _parse_arguments(_attr(item, "arguments")),
                "item_type": item_type,
                "phase": "done",
            },
            raw=raw,
        )

    if item_type in _HOSTED_TOOL_CALL_TYPES:
        data: dict[str, Any] = {
            "item_type": item_type,
            **_hosted_tool_data(item, item_type=item_type),
        }
        run_item = RunItem(
            type=ItemTypes.HOSTED_TOOL_CALL,
            provider=provider,
            status=_completed_item_status(item),
            id=item_id or f"item_{id(item)}",
            data=dict(data),
            raw=item,
        )
        data["item"] = run_item
        return AgentEvent(
            type=EventTypes.MODEL_ITEM_COMPLETED,
            provider=provider,
            item_id=run_item.id,
            data=data,
            raw=raw,
        )

    if item_type in _ITEM_TYPE_TO_RUN_ITEM and item_type.startswith("mcp_"):
        data = {
            "item_type": item_type,
            **_mcp_item_data(item, item_type=item_type),
        }
        run_item = RunItem(
            type=_ITEM_TYPE_TO_RUN_ITEM[item_type],
            provider=provider,
            status=_mcp_item_status(item, item_type=item_type),
            id=item_id or f"item_{id(item)}",
            data=dict(data),
            raw=item,
        )
        data["item"] = run_item
        return AgentEvent(
            type=_completed_mcp_event_type(item_type),
            provider=provider,
            item_id=run_item.id,
            data=data,
            raw=raw,
        )

    return AgentEvent(
        type=EventTypes.MODEL_ITEM_COMPLETED,
        provider=provider,
        item_id=item_id,
        data={"item_type": item_type},
        raw=raw,
    )


def _build_provider_state(final_response: Any, *, provider: str) -> ProviderState:
    response_id = _attr(final_response, "id")
    output = _attr(final_response, "output") or []
    return ProviderState(
        provider=provider,
        previous_response_id=response_id,
        native_history=list(output) if isinstance(output, list) else [],
        continuation={"previous_response_id": response_id} if response_id else {},
    )


async def _get_final_response(stream: Any) -> Any:
    getter = getattr(stream, "get_final_response", None)
    if getter is None:
        return None
    result = getter()
    if hasattr(result, "__await__"):
        return await result
    return result


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _hosted_tool_data(item: Any, *, item_type: str) -> dict[str, Any]:
    data: dict[str, Any] = {"hosted_tool_type": _HOSTED_TOOL_CALL_TYPES[item_type]}
    for key in _HOSTED_TOOL_DATA_KEYS:
        value = _attr(item, key)
        if value is not None:
            data[key] = value
    return data


def _mcp_item_data(item: Any, *, item_type: str) -> dict[str, Any]:
    data: dict[str, Any] = {"mcp_item_type": item_type}
    for key in _MCP_ITEM_DATA_KEYS:
        value = _attr(item, key)
        if value is None:
            continue
        if key == "arguments":
            data[key] = _parse_arguments(value)
        else:
            data[key] = value
    return data


def _mcp_item_status(item: Any, *, item_type: str) -> ItemStatus:
    if item_type == "mcp_approval_request":
        return "requires_action"
    if _attr(item, "error") is not None:
        return "failed"
    return "completed"


def _completed_mcp_event_type(item_type: str) -> str:
    if item_type == "mcp_call":
        return EventTypes.MCP_CALL_COMPLETED
    if item_type == "mcp_approval_request":
        return EventTypes.MCP_APPROVAL_REQUIRED
    if item_type == "mcp_list_tools":
        return EventTypes.MCP_LIST_TOOLS_COMPLETED
    return EventTypes.MODEL_ITEM_COMPLETED


def _completed_item_status(item: Any) -> ItemStatus:
    status = _attr(item, "status")
    if status == "failed":
        return "failed"
    return "completed"


def _parse_arguments(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {"_raw": value}
        except json.JSONDecodeError:
            return {"_raw": value}
    return {"_raw": value}
