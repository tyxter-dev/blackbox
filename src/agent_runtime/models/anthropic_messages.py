"""Provider-native adapter for the Anthropic Messages API.

Maps native streaming events to ``AgentEvent`` instances while preserving raw
provider payloads. Stable block types get typed events:

- ``text`` blocks become ``MODEL_ITEM_CREATED`` plus ``MODEL_TEXT_DELTA`` per
  ``text_delta`` and a final ``MODEL_ITEM_COMPLETED``.
- ``thinking`` blocks become reasoning items with ``MODEL_REASONING_DELTA``
  for each ``thinking_delta``.
- ``tool_use`` blocks accumulate ``input_json_delta`` chunks and emit
  ``TOOL_CALL_REQUESTED`` once the block stops, with ``input`` parsed.
- Other blocks (``server_tool_use``, ``web_search_tool_result``, future
  hosted tools) fall back to ``MODEL_ITEM_CREATED`` with the original block
  type stashed in ``data['item_type']`` and the raw block preserved.

``ProviderState.native_history`` holds the full Anthropic-shaped ``messages``
array for native multi-turn continuation.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from agent_runtime.core.accounting import usage_from_anthropic_message
from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.errors import ProviderExecutionError, ProviderNotConfiguredError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.hosted_tools import to_anthropic_tool
from agent_runtime.providers.base import TurnRequest


class AnthropicMessagesProvider:
    """Provider-native adapter for the Anthropic Messages API."""

    provider_id = "anthropic"

    def __init__(self, *, api_key: str | None = None, client: Any | None = None) -> None:
        self.api_key = api_key
        self._client = client

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            supports_streaming_events=True,
            supports_function_tools=True,
            supports_parallel_tool_calls=True,
            supports_hosted_tools=True,
            supports_remote_mcp=True,
            supports_reasoning_items=True,
            supports_provider_state=True,
            supports_structured_output=False,
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise ProviderNotConfiguredError(
                "AnthropicMessagesProvider requires either a client or an api_key."
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "AnthropicMessagesProvider needs the 'anthropic' package; install with the "
                "[anthropic] extra."
            ) from exc
        self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        client = self._get_client()
        messages = _compose_messages(request)
        kwargs = self._build_request_kwargs(request, messages)

        yield AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            provider=self.provider_id,
            data={"model": request.model},
        )

        partial_blocks: dict[int, dict[str, Any]] = {}
        json_buffers: dict[int, list[str]] = {}

        try:
            async with client.messages.stream(**kwargs) as stream:
                async for sdk_event in stream:
                    mapped = _map_event(
                        sdk_event,
                        provider=self.provider_id,
                        partial_blocks=partial_blocks,
                        json_buffers=json_buffers,
                    )
                    if mapped is not None:
                        yield mapped
                final_message = await _get_final_message(stream)
        except ProviderExecutionError:
            raise
        except Exception as exc:
            raise ProviderExecutionError(
                f"Anthropic Messages stream failed: {exc!s}"
            ) from exc

        provider_state = _build_provider_state(messages, final_message)
        usage = usage_from_anthropic_message(final_message)
        data: dict[str, Any] = {"model": request.model, "provider_state": provider_state}
        if usage is not None:
            data["usage"] = usage.to_dict()
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider=self.provider_id,
            data=data,
            raw=final_message,
        )

    @staticmethod
    def _build_request_kwargs(
        request: TurnRequest, messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        extra = dict(request.extra)
        tools_from_extra = extra.pop("tools", None)
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": 1024,
        }
        tools = [*request.tools, *(to_anthropic_tool(tool) for tool in request.hosted_tools)]
        if not tools:
            tools = tools_from_extra
        if tools:
            kwargs["tools"] = _convert_tools(tools)
        controls = request.controls
        if controls.instructions is not None:
            kwargs["system"] = controls.instructions
        if controls.temperature is not None:
            kwargs["temperature"] = controls.temperature
        if controls.top_p is not None:
            kwargs["top_p"] = controls.top_p
        if controls.max_output_tokens is not None:
            kwargs["max_tokens"] = controls.max_output_tokens
        if controls.tool_choice is not None:
            kwargs["tool_choice"] = controls.tool_choice
        kwargs.update(extra)
        return kwargs


def _compose_messages(request: TurnRequest) -> list[dict[str, Any]]:
    """Build the Anthropic ``messages`` array from prior history and new input."""
    prior: list[dict[str, Any]] = []
    if request.provider_state and isinstance(request.provider_state.native_history, list):
        prior = [dict(m) for m in request.provider_state.native_history if isinstance(m, dict)]

    if isinstance(request.input, str):
        return [*prior, {"role": "user", "content": request.input}]

    tool_results: list[dict[str, Any]] = []
    passthrough: list[dict[str, Any]] = []
    for entry in request.input:
        if isinstance(entry, RunItem) and entry.type == ItemTypes.FUNCTION_RESULT:
            tool_use_id = entry.data.get("call_id", "")
            content = entry.data.get("content", entry.data.get("error", ""))
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content if isinstance(content, str) else json.dumps(content),
            }
            if entry.status == "failed":
                block["is_error"] = True
            tool_results.append(block)
        elif isinstance(entry, dict):
            passthrough.append(entry)

    new_turns: list[dict[str, Any]] = []
    if tool_results:
        new_turns.append({"role": "user", "content": tool_results})
    new_turns.extend(passthrough)
    return [*prior, *new_turns]


def _convert_tools(tools: list[Any]) -> list[dict[str, Any]]:
    """Convert provider-neutral tool descriptors to Anthropic ``tools`` schema."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            converted.append(tool)
            continue
        if "input_schema" in tool:
            converted.append(tool)
            continue
        if tool.get("type") not in {None, "function"}:
            converted.append(tool)
            continue
        converted.append(
            {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "input_schema": tool.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return converted


def _map_event(
    sdk_event: Any,
    *,
    provider: str,
    partial_blocks: dict[int, dict[str, Any]],
    json_buffers: dict[int, list[str]],
) -> AgentEvent | None:
    event_type = getattr(sdk_event, "type", None)
    if not isinstance(event_type, str):
        return None

    if event_type == "content_block_start":
        index = _attr(sdk_event, "index")
        block = _attr(sdk_event, "content_block")
        return _map_block_start(
            block, index=index, provider=provider, raw=sdk_event,
            partial_blocks=partial_blocks, json_buffers=json_buffers,
        )

    if event_type == "content_block_delta":
        index = _attr(sdk_event, "index")
        delta = _attr(sdk_event, "delta")
        return _map_block_delta(
            delta, index=index, provider=provider, raw=sdk_event,
            partial_blocks=partial_blocks, json_buffers=json_buffers,
        )

    if event_type == "content_block_stop":
        index = _attr(sdk_event, "index")
        return _map_block_stop(
            index=index, provider=provider, raw=sdk_event,
            partial_blocks=partial_blocks, json_buffers=json_buffers,
        )

    if event_type in {"message_start", "message_delta", "message_stop"}:
        return None

    return AgentEvent(
        type=EventTypes.MODEL_ITEM_CREATED,
        provider=provider,
        data={"item_type": event_type, "passthrough": True},
        raw=sdk_event,
    )


def _map_block_start(
    block: Any,
    *,
    index: Any,
    provider: str,
    raw: Any,
    partial_blocks: dict[int, dict[str, Any]],
    json_buffers: dict[int, list[str]],
) -> AgentEvent | None:
    if block is None or not isinstance(index, int):
        return None
    block_type = _attr(block, "type")
    if not isinstance(block_type, str):
        return None

    block_id = _attr(block, "id")
    state: dict[str, Any] = {"type": block_type, "block": block, "id": block_id}
    if block_type == "tool_use":
        state["name"] = _attr(block, "name") or ""
        state["call_id"] = block_id or ""
        json_buffers[index] = []
    partial_blocks[index] = state

    canonical = EventTypes.MODEL_ITEM_CREATED
    run_item_type = {
        "text": ItemTypes.MESSAGE,
        "thinking": ItemTypes.REASONING,
        "tool_use": ItemTypes.FUNCTION_CALL,
    }.get(block_type, block_type)

    item_id = block_id or f"block_{index}"
    data: dict[str, Any] = {"item_type": block_type, "index": index}
    if block_type == "tool_use":
        data["call_id"] = state["call_id"]
        data["name"] = state["name"]

    run_item = RunItem(
        type=run_item_type,
        provider=provider,
        status="created",
        id=item_id,
        data=dict(data),
        raw=block,
    )
    data["item"] = run_item

    return AgentEvent(
        type=canonical,
        provider=provider,
        item_id=item_id,
        data=data,
        raw=raw,
    )


def _map_block_delta(
    delta: Any,
    *,
    index: Any,
    provider: str,
    raw: Any,
    partial_blocks: dict[int, dict[str, Any]],
    json_buffers: dict[int, list[str]],
) -> AgentEvent | None:
    if delta is None or not isinstance(index, int):
        return None
    delta_type = _attr(delta, "type")
    state = partial_blocks.get(index)
    item_id = state.get("id") if state else None

    if delta_type == "text_delta":
        return AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA,
            provider=provider,
            item_id=item_id,
            data={"delta": _attr(delta, "text") or "", "index": index},
            raw=raw,
        )

    if delta_type == "thinking_delta":
        return AgentEvent(
            type=EventTypes.MODEL_REASONING_DELTA,
            provider=provider,
            item_id=item_id,
            data={"delta": _attr(delta, "thinking") or "", "index": index},
            raw=raw,
        )

    if delta_type == "input_json_delta":
        chunk = _attr(delta, "partial_json") or ""
        json_buffers.setdefault(index, []).append(chunk)
        return None

    if delta_type == "signature_delta":
        return None

    return None


def _map_block_stop(
    *,
    index: Any,
    provider: str,
    raw: Any,
    partial_blocks: dict[int, dict[str, Any]],
    json_buffers: dict[int, list[str]],
) -> AgentEvent | None:
    if not isinstance(index, int):
        return None
    state = partial_blocks.pop(index, None)
    if state is None:
        return None
    block_type = state["type"]
    item_id = state.get("id") or f"block_{index}"

    if block_type == "tool_use":
        joined = "".join(json_buffers.pop(index, []))
        arguments = _parse_arguments(joined)
        return AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider=provider,
            item_id=item_id,
            data={
                "call_id": state.get("call_id", ""),
                "name": state.get("name", ""),
                "arguments": arguments,
                "item_type": block_type,
                "index": index,
                "phase": "done",
            },
            raw=raw,
        )

    return AgentEvent(
        type=EventTypes.MODEL_ITEM_COMPLETED,
        provider=provider,
        item_id=item_id,
        data={"item_type": block_type, "index": index},
        raw=raw,
    )


def _build_provider_state(
    messages: list[dict[str, Any]], final_message: Any
) -> ProviderState:
    history: list[dict[str, Any]] = list(messages)
    assistant_content = _final_message_content(final_message)
    if assistant_content is not None:
        history.append({"role": "assistant", "content": assistant_content})
    message_id = _attr(final_message, "id") or ""
    return ProviderState(
        provider="anthropic",
        native_history=history,
        continuation={"last_message_id": message_id} if message_id else {},
    )


def _final_message_content(final_message: Any) -> Any:
    if final_message is None:
        return None
    content = _attr(final_message, "content")
    if content is None:
        return None
    if isinstance(content, list):
        normalized: list[Any] = []
        for block in content:
            if isinstance(block, dict):
                normalized.append(block)
            else:
                normalized.append(_block_to_dict(block))
        return normalized
    return content


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Best-effort conversion of an SDK block object into a serializable dict."""
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        try:
            result = dump()
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    block_type = _attr(block, "type") or "unknown"
    out: dict[str, Any] = {"type": block_type}
    for name in ("text", "thinking", "id", "name", "input", "tool_use_id", "content"):
        value = _attr(block, name)
        if value is not None:
            out[name] = value
    return out


async def _get_final_message(stream: Any) -> Any:
    getter = getattr(stream, "get_final_message", None)
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
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {"_raw": value}
        except json.JSONDecodeError:
            return {"_raw": value}
    return {"_raw": value}
