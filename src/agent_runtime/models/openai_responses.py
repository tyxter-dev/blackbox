from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.errors import ProviderExecutionError, ProviderNotConfiguredError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.providers.base import TurnRequest


_TYPED_ITEM_EVENTS = {
    "message": EventTypes.MODEL_ITEM_CREATED,
    "reasoning": EventTypes.MODEL_ITEM_CREATED,
    "function_call": EventTypes.TOOL_CALL_REQUESTED,
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


class OpenAIResponsesProvider:
    """Provider-native adapter for the OpenAI Responses API.

    Maps native streaming events to AgentEvents while preserving raw payloads.
    Stable item types get typed events; evolving hosted tools fall back to
    MODEL_ITEM_CREATED with the original item type stashed in ``data['item_type']``.
    """

    provider_id = "openai"

    def __init__(self, *, api_key: str | None = None, client: Any | None = None) -> None:
        self.api_key = api_key
        self._client = client

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
        if not self.api_key:
            raise ProviderNotConfiguredError(
                "OpenAIResponsesProvider requires either a client or an api_key."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "OpenAIResponsesProvider needs the 'openai' package; install with the "
                "[openai] extra."
            ) from exc
        self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        client = self._get_client()
        kwargs = self._build_request_kwargs(request)

        yield AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            provider=self.provider_id,
            data={"model": request.model},
        )

        try:
            async with client.responses.stream(**kwargs) as stream:
                async for sdk_event in stream:
                    mapped = _map_event(sdk_event, provider=self.provider_id)
                    if mapped is not None:
                        yield mapped
                final_response = await _get_final_response(stream)
        except ProviderExecutionError:
            raise
        except Exception as exc:
            raise ProviderExecutionError(
                f"OpenAI Responses stream failed: {exc!s}"
            ) from exc

        provider_state = _build_provider_state(final_response)
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider=self.provider_id,
            data={"model": request.model, "provider_state": provider_state},
            raw=final_response,
        )

    @staticmethod
    def _build_request_kwargs(request: TurnRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "input": _coerce_input(request.input),
        }
        if request.tools:
            kwargs["tools"] = list(request.tools)
        if request.provider_state:
            previous = (
                request.provider_state.previous_response_id
                or request.provider_state.continuation.get("previous_response_id")
            )
            if previous:
                kwargs["previous_response_id"] = previous
        kwargs.update(request.extra)
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
            converted.append(entry)
    return converted


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
    if item_type == "function_call":
        data["call_id"] = _attr(item, "call_id") or item_id or ""
        data["name"] = _attr(item, "name") or ""
        data["arguments"] = _parse_arguments(_attr(item, "arguments"))

    run_item = RunItem(
        type=run_item_type,
        provider=provider,
        status="created",
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

    return AgentEvent(
        type=EventTypes.MODEL_ITEM_COMPLETED,
        provider=provider,
        item_id=item_id,
        data={"item_type": item_type},
        raw=raw,
    )


def _build_provider_state(final_response: Any) -> ProviderState:
    response_id = _attr(final_response, "id")
    output = _attr(final_response, "output") or []
    return ProviderState(
        provider="openai",
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
