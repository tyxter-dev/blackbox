"""Provider-native adapter for the Anthropic Messages API.

Maps native streaming events to ``AgentEvent`` instances while preserving raw
provider payloads. Stable block types get typed events:

- ``text`` blocks become ``MODEL_ITEM_CREATED`` plus ``MODEL_TEXT_DELTA`` per
  ``text_delta`` and a final ``MODEL_ITEM_COMPLETED``.
- ``thinking`` blocks become reasoning items with ``MODEL_REASONING_DELTA``
  for each ``thinking_delta``.
- ``tool_use`` blocks accumulate ``input_json_delta`` chunks and emit
  ``TOOL_CALL_REQUESTED`` once the block stops, with ``input`` parsed.
- ``mcp_tool_use`` / ``mcp_tool_result`` blocks become MCP call run items.
- Other blocks (``server_tool_use``, ``web_search_tool_result``, future
  hosted tools) fall back to ``MODEL_ITEM_CREATED`` with the original block
  type stashed in ``data['item_type']`` and the raw block preserved.

``ProviderState.native_history`` holds the full Anthropic-shaped ``messages``
array for native multi-turn continuation.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, cast

from agent_runtime.core.accounting import usage_from_anthropic_message
from agent_runtime.core.capabilities import (
    CapabilityDetail,
    HostedToolSupport,
    ModelCapabilities,
    ModelCapabilityProfile,
)
from agent_runtime.core.errors import (
    ProviderExecutionError,
    ProviderNotConfiguredError,
    UnsupportedFeatureError,
)
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemStatus, ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.hosted_tools import (
    anthropic_beta_values,
    anthropic_deferred_tools,
    anthropic_mcp_servers,
    to_anthropic_tool,
)
from agent_runtime.models._support import (
    is_retryable_status_error,
    sleep_for_retry,
    strip_private_fields,
)
from agent_runtime.providers.base import TurnRequest


class AnthropicMessagesProvider:
    """Provider-native adapter for the Anthropic Messages API."""

    provider_id = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        timeout: float | None = None,
        max_retries: int = 2,
        retry_min_delay: float = 0.5,
    ) -> None:
        self.api_key = api_key
        self._client = client
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
            supports_remote_mcp=True,
            supports_reasoning_items=True,
            supports_provider_state=True,
            supports_structured_output=False,
            hosted_tools={
                "web_search": HostedToolSupport("web_search", True, True, True, False),
                "web_fetch": HostedToolSupport("web_fetch", True, True, True, False),
                "code_interpreter": HostedToolSupport(
                    "code_interpreter", True, True, True, False
                ),
                "tool_search": HostedToolSupport("tool_search", True, True, True, False),
                "bash": HostedToolSupport(
                    "bash", True, True, False, True, requires_handler=True
                ),
                "computer_use": HostedToolSupport(
                    "computer_use", True, True, False, True, requires_handler=True
                ),
                "remote_mcp": HostedToolSupport("remote_mcp", True, True, True, False),
            },
        )

    def capability_profile(self, model: str | None = None) -> ModelCapabilityProfile:
        summary = self.capabilities(model)
        return ModelCapabilityProfile(
            provider=self.provider_id,
            model=model,
            hosted_tools={
                "remote_mcp": CapabilityDetail(
                    status="supported", native_name="mcp_servers/mcp_toolset"
                ),
                "web_search": CapabilityDetail(
                    status="supported", native_name="web_search"
                ),
                "web_fetch": CapabilityDetail(status="supported", native_name="web_fetch"),
                "code_interpreter": CapabilityDetail(
                    status="supported", native_name="code_execution"
                ),
                "tool_search": CapabilityDetail(
                    status="supported", native_name="tool_search"
                ),
                "bash": CapabilityDetail(
                    status="supported",
                    native_name="bash",
                    requires=("hosted_tool_handler",),
                ),
                "computer_use": CapabilityDetail(
                    status="supported",
                    native_name="computer",
                    requires=("hosted_tool_handler",),
                ),
                "text_editor": CapabilityDetail(status="unsupported"),
                "raw": CapabilityDetail(status="passthrough"),
            },
            output_strategies={
                "provider_native": CapabilityDetail(
                    status="unsupported",
                    reason="Structured output beta is not mapped by AnthropicMessagesProvider.",
                ),
                "finalizer_tool": CapabilityDetail(status="supported"),
                "posthoc_parse": CapabilityDetail(status="supported"),
                "posthoc_parse_with_retry": CapabilityDetail(status="supported"),
            },
            controls={
                "instructions": CapabilityDetail(status="supported", native_name="system"),
                "temperature": CapabilityDetail(status="supported", native_name="temperature"),
                "top_p": CapabilityDetail(status="supported", native_name="top_p"),
                "max_output_tokens": CapabilityDetail(
                    status="supported", native_name="max_tokens"
                ),
                "tool_choice": CapabilityDetail(status="supported", native_name="tool_choice"),
                "parallel_tool_calls": CapabilityDetail(status="supported"),
                "cache": CapabilityDetail(status="supported"),
                "cache_ttl": CapabilityDetail(status="supported", native_name="cache_control.ttl"),
                "cache_breakpoints": CapabilityDetail(
                    status="supported", native_name="cache_control"
                ),
                "reasoning_effort": CapabilityDetail(
                    status="supported",
                    native_name="thinking.budget_tokens",
                    supported_values=("minimal", "low", "medium", "high", "xhigh"),
                ),
                "tool_search": CapabilityDetail(status="unsupported"),
                "compaction": CapabilityDetail(status="unsupported"),
                "modalities": CapabilityDetail(status="unsupported"),
                "extra": CapabilityDetail(status="passthrough"),
            },
            state_modes={
                "none": CapabilityDetail(status="supported"),
                "provider_stateful": CapabilityDetail(
                    status="unsupported",
                    reason="Messages adapter replays native message history rather than using a "
                    "provider-side conversation ID.",
                ),
                "stateless_replay": CapabilityDetail(
                    status="supported", native_name="messages/native_history"
                ),
                "zdr": CapabilityDetail(status="conditional"),
                "encrypted_reasoning": CapabilityDetail(
                    status="unsupported",
                    reason="Adapter preserves thinking blocks but does not expose encrypted "
                    "reasoning state mode.",
                ),
            },
            summary=summary,
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderNotConfiguredError(
                "AnthropicMessagesProvider requires either a client, an api_key, "
                "or ANTHROPIC_API_KEY."
            )
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "AnthropicMessagesProvider needs the 'anthropic' package; install with the "
                "[anthropic] extra."
            ) from exc
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if self.timeout is not None:
            client_kwargs["timeout"] = self.timeout
        self._client = AsyncAnthropic(**client_kwargs)
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
        messages = _compose_messages(request)
        kwargs = self._build_request_kwargs(request, messages)

        yield AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            provider=self.provider_id,
            data={"model": request.model},
        )

        partial_blocks: dict[int, dict[str, Any]] = {}
        json_buffers: dict[int, list[str]] = {}

        attempt = 0
        while True:
            emitted_provider_output = False
            partial_blocks.clear()
            json_buffers.clear()
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
                            emitted_provider_output = True
                            yield mapped
                    final_message = await _get_final_message(stream)
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
                        f"Anthropic Messages stream failed: {exc!s}"
                    ) from exc
                await sleep_for_retry(
                    exc, attempt=attempt, base_delay=self.retry_min_delay
                )
                attempt += 1

        provider_state = _build_provider_state(messages, final_message)
        usage = usage_from_anthropic_message(final_message)
        data: dict[str, Any] = {"model": request.model, "provider_state": provider_state}
        if usage is not None:
            data["usage"] = usage.to_dict()
            if usage.provider_details:
                data["usage_provider_details"] = usage.provider_details
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider=self.provider_id,
            data=data,
            raw=final_message,
        )

    def _is_retryable_error(self, error: Exception) -> bool:
        try:
            from anthropic import APIConnectionError, APIStatusError, APITimeoutError
        except ImportError:
            return is_retryable_status_error(error)
        if isinstance(error, (APIConnectionError, APITimeoutError)):
            return True
        return isinstance(error, APIStatusError) and is_retryable_status_error(error)

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
        tools = [
            *request.tools,
            *anthropic_deferred_tools(request.hosted_tools),
            *(to_anthropic_tool(tool, model=request.model) for tool in request.hosted_tools),
        ]
        if not tools:
            tools = tools_from_extra
        if tools:
            kwargs["tools"] = _convert_tools(tools)
        mcp_servers = anthropic_mcp_servers(request.hosted_tools)
        if mcp_servers:
            kwargs["mcp_servers"] = mcp_servers
        beta_values = anthropic_beta_values(request.hosted_tools)
        if beta_values:
            existing_betas = extra.pop("betas", []) or []
            kwargs["betas"] = [*existing_betas, *beta_values]
        controls = request.controls
        if controls.instructions is not None:
            kwargs["system"] = _system_payload(controls.instructions, controls.cache)
        if controls.temperature is not None:
            kwargs["temperature"] = controls.temperature
        if controls.top_p is not None:
            kwargs["top_p"] = controls.top_p
        if controls.max_output_tokens is not None:
            kwargs["max_tokens"] = controls.max_output_tokens
        if controls.reasoning_effort is not None:
            _validate_anthropic_thinking_controls(controls)
            thinking, max_tokens = _anthropic_thinking_config(
                controls.reasoning_effort,
                max_tokens=kwargs["max_tokens"],
                explicit_max_tokens=controls.max_output_tokens is not None,
            )
            if thinking is not None:
                kwargs["max_tokens"] = max_tokens
                kwargs["thinking"] = thinking
        if controls.tool_choice is not None:
            kwargs["tool_choice"] = controls.tool_choice
        kwargs.update(extra)
        return kwargs


_ANTHROPIC_THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
}


def _validate_anthropic_thinking_controls(controls: Any) -> None:
    if controls.temperature is not None:
        raise UnsupportedFeatureError(
            "Anthropic thinking is not compatible with temperature; omit temperature."
        )
    if controls.top_p is not None and not 0.95 <= controls.top_p <= 1:
        raise UnsupportedFeatureError(
            "Anthropic thinking requires top_p to be between 0.95 and 1."
        )
    if controls.tool_choice is None:
        return
    tool_choice = controls.tool_choice
    if isinstance(tool_choice, str) and tool_choice in {"auto", "none"}:
        return
    if isinstance(tool_choice, dict) and tool_choice.get("type") in {"auto", "none"}:
        return
    raise UnsupportedFeatureError(
        "Anthropic thinking only supports tool_choice auto or none."
    )


def _anthropic_thinking_config(
    effort: str,
    *,
    max_tokens: int,
    explicit_max_tokens: bool,
) -> tuple[dict[str, Any], int]:
    budget = _ANTHROPIC_THINKING_BUDGETS.get(effort)
    if budget is None:
        raise UnsupportedFeatureError(
            f"Unsupported Anthropic reasoning_effort={effort!r}; expected one of "
            f"{tuple(_ANTHROPIC_THINKING_BUDGETS)}."
        )
    if budget >= max_tokens:
        if explicit_max_tokens:
            raise UnsupportedFeatureError(
                "Anthropic thinking budget must be lower than max_output_tokens."
            )
        max_tokens = budget + 1024
    return {"type": "enabled", "budget_tokens": budget}, max_tokens


def _compose_messages(request: TurnRequest) -> list[dict[str, Any]]:
    """Build the Anthropic ``messages`` array from prior history and new input."""
    prior: list[dict[str, Any]] = []
    if request.provider_state and isinstance(request.provider_state.native_history, list):
        prior = [dict(m) for m in request.provider_state.native_history if isinstance(m, dict)]

    if isinstance(request.input, str):
        content: str | list[dict[str, Any]] = request.input
        cache_control = _cache_control_payload(request.controls.cache)
        if cache_control is not None:
            content = [{"type": "text", "text": request.input, "cache_control": cache_control}]
        return [*prior, {"role": "user", "content": content}]

    tool_results: list[dict[str, Any]] = []
    passthrough: list[dict[str, Any]] = []
    for entry in request.input:
        if isinstance(entry, RunItem) and entry.type == ItemTypes.HOSTED_TOOL_RESULT:
            provider_item = entry.data.get("provider_input_item")
            if isinstance(provider_item, dict):
                tool_results.append(provider_item)
        elif isinstance(entry, RunItem) and entry.type == ItemTypes.FUNCTION_RESULT:
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
            passthrough.append(_cache_marked_entry(entry, request.controls.cache))

    new_turns: list[dict[str, Any]] = []
    if tool_results:
        new_turns.append({"role": "user", "content": tool_results})
    new_turns.extend(passthrough)
    return [*prior, *new_turns]


def _system_payload(instructions: str, cache: Any) -> str | list[dict[str, Any]]:
    cache_control = _cache_control_payload(cache)
    if cache_control is None:
        return instructions
    return [{"type": "text", "text": instructions, "cache_control": cache_control}]


def _cache_control_payload(cache: Any) -> dict[str, Any] | None:
    if cache is None or cache.strategy == "bypass":
        return None
    if cache.strategy not in {"auto", "ephemeral"}:
        return None
    payload: dict[str, Any] = {"type": "ephemeral"}
    if cache.ttl is not None:
        payload["ttl"] = cache.ttl
    payload.update(cache.extra)
    return payload


def _cache_marked_entry(entry: dict[str, Any], cache: Any) -> dict[str, Any]:
    cache_control = entry.get("_cache_control") or _cache_control_payload(cache)
    converted = strip_private_fields(entry)
    if cache_control is None:
        return cast(dict[str, Any], converted)
    content = converted.get("content")
    if isinstance(content, str):
        converted["content"] = [
            {"type": "text", "text": content, "cache_control": dict(cache_control)}
        ]
    elif isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict) and "cache_control" not in last:
            updated = dict(last)
            updated["cache_control"] = dict(cache_control)
            converted["content"] = [*content[:-1], updated]
    return cast(dict[str, Any], converted)


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
    if block_type in {"mcp_tool_use", "mcp_tool_result"}:
        state.update(_mcp_block_data(block, block_type=block_type))
    partial_blocks[index] = state

    canonical = EventTypes.MODEL_ITEM_CREATED
    run_item_type = {
        "text": ItemTypes.MESSAGE,
        "thinking": ItemTypes.REASONING,
        "tool_use": ItemTypes.FUNCTION_CALL,
        "mcp_tool_use": ItemTypes.MCP_CALL,
        "mcp_tool_result": ItemTypes.MCP_CALL,
        "server_tool_use": ItemTypes.HOSTED_TOOL_CALL,
        "web_search_tool_result": ItemTypes.HOSTED_TOOL_RESULT,
        "web_fetch_tool_result": ItemTypes.HOSTED_TOOL_RESULT,
        "code_execution_tool_result": ItemTypes.HOSTED_TOOL_RESULT,
        "tool_search_tool_result": ItemTypes.TOOL_SEARCH_OUTPUT,
        "tool_reference": ItemTypes.TOOL_SEARCH_OUTPUT,
    }.get(block_type, block_type)
    if block_type == "mcp_tool_use":
        canonical = EventTypes.MCP_CALL_STARTED
    elif block_type == "mcp_tool_result":
        canonical = EventTypes.MCP_CALL_COMPLETED
    elif block_type == "tool_search_tool_result" or block_type == "tool_reference":
        canonical = EventTypes.TOOL_SEARCH_COMPLETED
    elif block_type in {
        "server_tool_use",
        "web_search_tool_result",
        "web_fetch_tool_result",
        "code_execution_tool_result",
    }:
        canonical = (
            EventTypes.HOSTED_TOOL_CALL_COMPLETED
            if block_type.endswith("_tool_result")
            else EventTypes.MODEL_ITEM_CREATED
        )

    item_id = block_id or f"block_{index}"
    data: dict[str, Any] = {"item_type": block_type, "index": index}
    if block_type == "tool_use":
        data["call_id"] = state["call_id"]
        data["name"] = state["name"]
    elif block_type in {"mcp_tool_use", "mcp_tool_result"}:
        data.update(_mcp_block_data(block, block_type=block_type))
    elif run_item_type in {ItemTypes.HOSTED_TOOL_CALL, ItemTypes.HOSTED_TOOL_RESULT}:
        data.update(_hosted_block_data(block, block_type=block_type))

    run_item = RunItem(
        type=run_item_type,
        provider=provider,
        status=_block_status(block, block_type=block_type),
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
        hosted_tool_type = _anthropic_client_tool_type(str(state.get("name", "")))
        if hosted_tool_type is not None:
            data = {
                "call_id": state.get("call_id", ""),
                "name": state.get("name", ""),
                "arguments": arguments,
                "item_type": block_type,
                "provider_item_type": block_type,
                "hosted_tool_type": hosted_tool_type,
                "requires_continuation": True,
                "index": index,
                "phase": "done",
            }
            item = RunItem(
                type=ItemTypes.HOSTED_TOOL_CALL,
                provider=provider,
                status="requires_action",
                id=item_id,
                data=dict(data),
                raw=state.get("block"),
            )
            data["item"] = item
            return AgentEvent(
                type=EventTypes.HOSTED_TOOL_CALL_REQUESTED,
                provider=provider,
                item_id=item_id,
                data=data,
                raw=raw,
            )
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

    if block_type in {"mcp_tool_use", "mcp_tool_result"}:
        return None

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


def _mcp_block_data(block: Any, *, block_type: str) -> dict[str, Any]:
    data: dict[str, Any] = {"mcp_item_type": block_type}
    call_id = _attr(block, "id") or _attr(block, "tool_use_id")
    server_name = _attr(block, "server_name")
    name = _attr(block, "name")
    input_value = _attr(block, "input")
    content = _attr(block, "content")
    is_error = _attr(block, "is_error")
    if call_id is not None:
        data["call_id"] = call_id
    if server_name is not None:
        data["server_label"] = server_name
    if name is not None:
        data["name"] = name
    if input_value is not None:
        data["arguments"] = input_value
    if content is not None:
        data["output"] = content
    if is_error is not None:
        data["is_error"] = is_error
    return data


def _hosted_block_data(block: Any, *, block_type: str) -> dict[str, Any]:
    data: dict[str, Any] = {"hosted_tool_type": _anthropic_server_tool_type(block, block_type)}
    call_id = _attr(block, "id") or _attr(block, "tool_use_id")
    if call_id is not None:
        data["call_id"] = call_id
    name = _attr(block, "name")
    if name is not None:
        data["name"] = name
    input_value = _attr(block, "input")
    if input_value is not None:
        data["arguments"] = input_value
    content = _attr(block, "content")
    if content is not None:
        data["output"] = content
    is_error = _attr(block, "is_error")
    if is_error is not None:
        data["is_error"] = is_error
    return data


def _anthropic_server_tool_type(block: Any, block_type: str) -> str:
    name = _attr(block, "name")
    if isinstance(name, str):
        if name == "web_search":
            return "web_search"
        if name == "web_fetch":
            return "web_fetch"
        if name == "code_execution":
            return "code_interpreter"
        if name == "tool_search":
            return "tool_search"
    if block_type == "web_search_tool_result":
        return "web_search"
    if block_type == "web_fetch_tool_result":
        return "web_fetch"
    if block_type == "code_execution_tool_result":
        return "code_interpreter"
    return block_type.removesuffix("_tool_result")


def _anthropic_client_tool_type(name: str) -> str | None:
    if name == "bash":
        return "shell"
    if name == "computer":
        return "computer"
    if name in {"text_editor", "str_replace_editor"}:
        return "text_editor"
    return None


def _block_status(block: Any, *, block_type: str) -> ItemStatus:
    if block_type == "mcp_tool_result":
        return "failed" if _attr(block, "is_error") is True else "completed"
    return "created"


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
    for name in (
        "text",
        "thinking",
        "id",
        "name",
        "server_name",
        "input",
        "tool_use_id",
        "is_error",
        "content",
    ):
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
