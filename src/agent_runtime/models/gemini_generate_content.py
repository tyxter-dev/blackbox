"""Provider-native adapter for the Gemini GenerateContent API.

Maps streamed ``GenerateContentResponse`` chunks into canonical runtime
events. The adapter preserves Gemini-shaped content/part dictionaries in
``ProviderState.native_history`` rather than projecting them into chat
messages.
"""
from __future__ import annotations

import inspect
import os
from collections.abc import AsyncIterator
from typing import Any

from agent_runtime.core.accounting import add_usage, usage_from_gemini_chunk
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
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.hosted_tools import to_gemini_tool
from agent_runtime.models._support import (
    is_retryable_status_error,
    sleep_for_retry,
    strip_private_fields,
)
from agent_runtime.providers.base import TurnRequest


class GeminiGenerateContentProvider:
    """Provider-native adapter for Google's Gemini GenerateContent API."""

    provider_id = "google"

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
            supports_remote_mcp=False,
            supports_reasoning_items=True,
            supports_provider_state=True,
            supports_structured_output=True,
            hosted_tools={
                "web_search": HostedToolSupport("web_search", True, True, True, False),
                "code_execution": HostedToolSupport(
                    "code_execution", True, True, True, False
                ),
                "url_context": HostedToolSupport("url_context", True, True, True, False),
                "file_search": HostedToolSupport("file_search", True, True, True, False),
                "computer": HostedToolSupport(
                    "computer", True, True, False, True, requires_handler=True
                ),
            },
        )

    def capability_profile(self, model: str | None = None) -> ModelCapabilityProfile:
        summary = self.capabilities(model)
        return ModelCapabilityProfile(
            provider=self.provider_id,
            model=model,
            hosted_tools={
                "web_search": CapabilityDetail(status="supported", native_name="google_search"),
                "file_search": CapabilityDetail(
                    status="unsupported",
                    reason="Gemini File Search exists, but adapter typed mapping is not part of "
                    "the stable granular contract yet.",
                ),
                "code_interpreter": CapabilityDetail(
                    status="unsupported",
                    reason="Gemini code execution exists, but adapter typed mapping is not part "
                    "of the stable granular contract yet.",
                ),
                "computer_use": CapabilityDetail(
                    status="unsupported",
                    reason="Adapter typed mapping is not part of the stable granular contract yet.",
                ),
                "url_context": CapabilityDetail(
                    status="unsupported",
                    reason="Adapter mapping exists but is not part of the stable granular "
                    "contract yet.",
                ),
                "remote_mcp": CapabilityDetail(status="unsupported"),
                "raw": CapabilityDetail(status="passthrough"),
            },
            output_strategies={
                "provider_native": CapabilityDetail(
                    status="supported", native_name="config.response_json_schema"
                ),
                "finalizer_tool": CapabilityDetail(status="supported"),
                "posthoc_parse": CapabilityDetail(status="supported"),
                "posthoc_parse_with_retry": CapabilityDetail(status="supported"),
            },
            controls={
                "instructions": CapabilityDetail(
                    status="supported", native_name="system_instruction"
                ),
                "temperature": CapabilityDetail(status="supported", native_name="temperature"),
                "top_p": CapabilityDetail(status="supported", native_name="top_p"),
                "max_output_tokens": CapabilityDetail(
                    status="supported", native_name="max_output_tokens"
                ),
                "tool_choice": CapabilityDetail(
                    status="unsupported",
                    reason="Current adapter does not map tool_choice.",
                ),
                "parallel_tool_calls": CapabilityDetail(
                    status="conditional",
                    reason="Capability summary says supported, but request mapping should be "
                    "verified.",
                ),
                "cache": CapabilityDetail(status="supported"),
                "cached_content": CapabilityDetail(
                    status="supported", native_name="cached_content"
                ),
                "cache_key": CapabilityDetail(status="unsupported"),
                "cache_ttl": CapabilityDetail(status="unsupported"),
                "reasoning_effort": CapabilityDetail(
                    status="unsupported",
                    reason="Current adapter does not map thinking controls.",
                ),
                "safety_settings": CapabilityDetail(
                    status="passthrough", native_name="config.safety_settings"
                ),
                "extra": CapabilityDetail(status="passthrough"),
            },
            state_modes={
                "none": CapabilityDetail(status="supported"),
                "provider_stateful": CapabilityDetail(
                    status="unsupported",
                    reason="GenerateContent adapter replays native history rather than using a "
                    "provider-side conversation ID.",
                ),
                "stateless_replay": CapabilityDetail(
                    status="supported",
                    reason="Adapter preserves native contents, thought signatures, and tool call "
                    "IDs.",
                ),
                "zdr": CapabilityDetail(status="conditional"),
                "encrypted_reasoning": CapabilityDetail(status="unsupported"),
            },
            summary=summary,
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = (
            self.api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        if not api_key:
            raise ProviderNotConfiguredError(
                "GeminiGenerateContentProvider requires either a client, an api_key, "
                "GOOGLE_API_KEY, or GEMINI_API_KEY."
            )
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "GeminiGenerateContentProvider needs the 'google-genai' package; "
                "install with the [google] extra."
            ) from exc
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if self.timeout is not None:
            client_kwargs["http_options"] = {"timeout": self.timeout * 1000}
        self._client = genai.Client(**client_kwargs)
        return self._client

    async def close(self) -> None:
        if self._client is None or not self._owns_client:
            return
        aio = getattr(self._client, "aio", None)
        aio_close = getattr(aio, "aclose", None)
        if callable(aio_close):
            await aio_close()
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

        response_id: str | None = None
        assistant_parts: list[dict[str, Any]] = []
        thought_signatures: list[Any] = []
        tool_call_ids: list[str] = []
        usage = None

        attempt = 0
        while True:
            emitted_provider_output = False
            assistant_parts.clear()
            thought_signatures.clear()
            tool_call_ids.clear()
            usage = None
            try:
                stream = client.aio.models.generate_content_stream(**kwargs)
                if inspect.isawaitable(stream):
                    stream = await stream
                async for chunk in stream:
                    response_id = _attr(chunk, "response_id") or response_id
                    usage = add_usage(usage, usage_from_gemini_chunk(chunk))
                    for event in _map_chunk(
                        chunk,
                        provider=self.provider_id,
                        assistant_parts=assistant_parts,
                        thought_signatures=thought_signatures,
                        tool_call_ids=tool_call_ids,
                    ):
                        emitted_provider_output = True
                        yield event
                break
            except ProviderExecutionError:
                raise
            except Exception as exc:
                if (
                    emitted_provider_output
                    or attempt >= self.max_retries
                    or not is_retryable_status_error(exc)
                ):
                    raise ProviderExecutionError(
                        f"Gemini GenerateContent stream failed: {exc!s}"
                    ) from exc
                await sleep_for_retry(
                    exc, attempt=attempt, base_delay=self.retry_min_delay
                )
                attempt += 1

        provider_state = _build_provider_state(
            request=request,
            assistant_parts=assistant_parts,
            response_id=response_id,
            thought_signatures=thought_signatures,
            tool_call_ids=tool_call_ids,
        )
        data: dict[str, Any] = {"model": request.model, "provider_state": provider_state}
        if usage is not None:
            data["usage"] = usage.to_dict()
            if usage.provider_details:
                data["usage_provider_details"] = usage.provider_details
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider=self.provider_id,
            data=data,
        )

    @staticmethod
    def _build_request_kwargs(request: TurnRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "contents": _compose_contents(request),
        }
        extra = dict(request.extra)
        config = dict(extra.pop("config", {})) if "config" in extra else {}
        tools: list[Any] = [
            *request.tools,
            *(to_gemini_tool(tool) for tool in request.hosted_tools),
        ]
        if not tools:
            config_tools = config.get("tools")
            if isinstance(config_tools, list):
                tools = config_tools
        if tools:
            config["tools"] = _convert_tools(tools)
        controls = request.controls
        if controls.instructions is not None:
            config.setdefault("system_instruction", controls.instructions)
        if controls.temperature is not None:
            config.setdefault("temperature", controls.temperature)
        if controls.top_p is not None:
            config.setdefault("top_p", controls.top_p)
        if controls.max_output_tokens is not None:
            config.setdefault("max_output_tokens", controls.max_output_tokens)
        if controls.cache is not None:
            if controls.cache.strategy == "bypass":
                raise UnsupportedFeatureError(
                    "Gemini prompt caching is provider-managed and cannot be bypassed through "
                    "ModelCacheControl."
                )
            if controls.cache.cached_content is not None:
                config.setdefault("cached_content", controls.cache.cached_content)
            config.update(controls.cache.extra)
        if request.output_schema is not None and request.output_strategy == "provider_native":
            _merge_output_schema_config(config, request)
        if config:
            kwargs["config"] = config
        kwargs.update(extra)
        return kwargs


def _merge_output_schema_config(config: dict[str, Any], request: TurnRequest) -> None:
    assert request.output_schema is not None
    existing_mime = config.get("response_mime_type")
    if existing_mime is not None and existing_mime != "application/json":
        raise ValueError(
            "config.response_mime_type conflicts with provider_native output schema."
        )
    existing_schema = config.get("response_json_schema")
    if existing_schema is not None and existing_schema != request.output_schema.schema:
        raise ValueError(
            "config.response_json_schema conflicts with provider_native output schema."
        )
    config["response_mime_type"] = "application/json"
    config["response_json_schema"] = request.output_schema.schema


def _compose_contents(request: TurnRequest) -> Any:
    prior: list[Any] = []
    if request.provider_state and isinstance(request.provider_state.native_history, list):
        prior = list(request.provider_state.native_history)

    if isinstance(request.input, str):
        return [*prior, {"role": "user", "parts": [{"text": request.input}]}]

    parts: list[dict[str, Any]] = []
    passthrough: list[Any] = []
    for entry in request.input:
        if isinstance(entry, RunItem) and entry.type == ItemTypes.FUNCTION_RESULT:
            name = entry.data.get("name") or entry.data.get("tool_name") or ""
            call_id = entry.data.get("call_id", "")
            content = entry.data.get("content", entry.data.get("error", ""))
            response = content if isinstance(content, dict) else {"result": content}
            parts.append(
                {
                    "function_response": {
                        "id": call_id,
                        "name": name,
                        "response": response,
                    }
                }
            )
        elif isinstance(entry, RunItem) and entry.type == ItemTypes.HOSTED_TOOL_RESULT:
            provider_item = entry.data.get("provider_input_item")
            if isinstance(provider_item, dict):
                parts.append(provider_item)
        else:
            passthrough.append(strip_private_fields(entry))

    if parts:
        return [*prior, {"role": "user", "parts": parts}, *passthrough]
    return [*prior, *passthrough]


def _convert_tools(tools: list[Any]) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    passthrough: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            passthrough.append(tool)
            continue
        if "function_declarations" in tool:
            passthrough.append(tool)
            continue
        if "name" not in tool and "parameters" not in tool and "description" not in tool:
            passthrough.append(tool)
            continue
        if tool.get("type") not in {None, "function"}:
            passthrough.append(tool)
            continue
        declarations.append(
            {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    if declarations:
        passthrough.insert(0, {"function_declarations": declarations})
    return passthrough


def _map_chunk(
    chunk: Any,
    *,
    provider: str,
    assistant_parts: list[dict[str, Any]],
    thought_signatures: list[Any],
    tool_call_ids: list[str],
) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    for candidate in _attr(chunk, "candidates") or []:
        content = _attr(candidate, "content")
        for index, part in enumerate(_attr(content, "parts") or []):
            part_dict = _part_to_dict(part)
            assistant_parts.append(part_dict)
            signature = _attr(part, "thought_signature")
            if signature is not None:
                thought_signatures.append(signature)

            function_call = _attr(part, "function_call")
            if function_call is not None:
                call_id = _attr(function_call, "id") or f"gemini_call_{len(tool_call_ids) + 1}"
                tool_call_ids.append(call_id)
                name = _attr(function_call, "name") or ""
                arguments = _coerce_args(_attr(function_call, "args"))
                item = RunItem(
                    type=ItemTypes.FUNCTION_CALL,
                    provider=provider,
                    status="created",
                    id=call_id,
                    data={"call_id": call_id, "name": name, "arguments": arguments},
                    raw=function_call,
                )
                events.append(
                    AgentEvent(
                        type=EventTypes.TOOL_CALL_REQUESTED,
                        provider=provider,
                        item_id=item.id,
                        data={
                            "call_id": call_id,
                            "name": name,
                            "arguments": arguments,
                            "item": item,
                            "index": index,
                        },
                        raw=part,
                    )
                )
                continue

            executable_code = _attr(part, "executable_code")
            if executable_code is not None:
                call_id = f"gemini_code_{len(assistant_parts)}"
                item = RunItem(
                    type=ItemTypes.HOSTED_TOOL_CALL,
                    provider=provider,
                    status="completed",
                    id=call_id,
                    data={
                        "call_id": call_id,
                        "hosted_tool_type": "code_execution",
                        "provider_item_type": "executable_code",
                        "code": _attr(executable_code, "code"),
                        "language": _attr(executable_code, "language"),
                    },
                    raw=executable_code,
                )
                events.append(
                    AgentEvent(
                        type=EventTypes.HOSTED_TOOL_CALL_COMPLETED,
                        provider=provider,
                        item_id=item.id,
                        data={**item.data, "item": item},
                        raw=part,
                    )
                )
                continue

            code_execution_result = _attr(part, "code_execution_result")
            if code_execution_result is not None:
                call_id = f"gemini_code_result_{len(assistant_parts)}"
                item = RunItem(
                    type=ItemTypes.HOSTED_TOOL_RESULT,
                    provider=provider,
                    status="completed",
                    id=call_id,
                    data={
                        "call_id": call_id,
                        "hosted_tool_type": "code_execution",
                        "provider_item_type": "code_execution_result",
                        "output": _attr(code_execution_result, "output"),
                        "outcome": _attr(code_execution_result, "outcome"),
                    },
                    raw=code_execution_result,
                )
                events.append(
                    AgentEvent(
                        type=EventTypes.HOSTED_TOOL_CALL_COMPLETED,
                        provider=provider,
                        item_id=item.id,
                        data={**item.data, "item": item},
                        raw=part,
                    )
                )
                continue

            text = _attr(part, "text")
            if isinstance(text, str) and text:
                if _attr(part, "thought") is True:
                    event_type = EventTypes.MODEL_REASONING_DELTA
                else:
                    event_type = EventTypes.MODEL_TEXT_DELTA
                events.append(
                    AgentEvent(
                        type=event_type,
                        provider=provider,
                        data={"delta": text, "index": index},
                        raw=part,
                    )
                )
                continue

            events.append(
                AgentEvent(
                    type=EventTypes.MODEL_ITEM_CREATED,
                    provider=provider,
                    data={"item_type": "part", "index": index, "passthrough": True},
                    raw=part,
                )
            )
    return events


def _build_provider_state(
    *,
    request: TurnRequest,
    assistant_parts: list[dict[str, Any]],
    response_id: str | None,
    thought_signatures: list[Any],
    tool_call_ids: list[str],
) -> ProviderState:
    history = _compose_contents(request)
    if not isinstance(history, list):
        history = [history]
    if assistant_parts:
        history = [*history, {"role": "model", "parts": assistant_parts}]
    return ProviderState(
        provider="google",
        conversation_id=response_id,
        native_history=history,
        reasoning_state={"thought_signatures": thought_signatures},
        tool_state={"tool_call_ids": tool_call_ids},
        continuation={"response_id": response_id} if response_id else {},
    )


def _part_to_dict(part: Any) -> dict[str, Any]:
    dump = getattr(part, "model_dump", None)
    if callable(dump):
        result = dump(exclude_none=True)
        if isinstance(result, dict):
            return result
    if isinstance(part, dict):
        return dict(part)
    out: dict[str, Any] = {}
    for name in ("text", "thought", "thought_signature"):
        value = _attr(part, name)
        if value is not None:
            out[name] = value
    function_call = _attr(part, "function_call")
    if function_call is not None:
        out["function_call"] = {
            "id": _attr(function_call, "id"),
            "name": _attr(function_call, "name"),
            "args": _coerce_args(_attr(function_call, "args")),
        }
    executable_code = _attr(part, "executable_code")
    if executable_code is not None:
        out["executable_code"] = {
            "language": _attr(executable_code, "language"),
            "code": _attr(executable_code, "code"),
        }
    code_execution_result = _attr(part, "code_execution_result")
    if code_execution_result is not None:
        out["code_execution_result"] = {
            "outcome": _attr(code_execution_result, "outcome"),
            "output": _attr(code_execution_result, "output"),
        }
    return out


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _coerce_args(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(exclude_none=True)
        return result if isinstance(result, dict) else {"_raw": result}
    return {"_raw": value}
