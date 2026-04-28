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
from datetime import UTC, datetime
from typing import Any

from agent_runtime.core.accounting import add_usage, usage_from_gemini_chunk
from agent_runtime.core.cache import ProviderCacheRecord
from agent_runtime.core.capabilities import (
    CapabilityConstraint,
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
from agent_runtime.providers.base import TurnRequest
from agent_runtime.providers.model_adapters._support import (
    is_retryable_status_error,
    sleep_for_retry,
    strip_private_fields,
)
from agent_runtime.tools.hosted.specs import to_gemini_tool


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
                "code_interpreter": HostedToolSupport(
                    "code_interpreter", True, True, True, False
                ),
                "url_context": HostedToolSupport("url_context", True, True, True, False),
                "file_search": HostedToolSupport("file_search", True, True, True, False),
                "computer_use": HostedToolSupport(
                    "computer_use", True, True, False, True, requires_handler=True
                ),
            },
        )

    def capability_profile(self, model: str | None = None) -> ModelCapabilityProfile:
        """Return Gemini GenerateContent capability details for validation and planning."""
        summary = self.capabilities(model)
        structured_with_tools = _gemini_supports_structured_output_with_tools(model)
        return ModelCapabilityProfile(
            provider=self.provider_id,
            model=model,
            hosted_tools={
                "web_search": CapabilityDetail(status="supported", native_name="google_search"),
                "file_search": CapabilityDetail(
                    status="conditional",
                    native_name="file_search",
                    reason="Requires Gemini corpus_ids or raw_config.",
                ),
                "code_interpreter": CapabilityDetail(
                    status="supported",
                    native_name="code_execution",
                ),
                "computer_use": CapabilityDetail(
                    status="conditional",
                    native_name="computer_use",
                    requires=("hosted_tool_handler",),
                ),
                "url_context": CapabilityDetail(
                    status="supported",
                    native_name="url_context",
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
                    status="supported",
                    native_name="config.tool_config.function_calling_config",
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
                    status="supported",
                    native_name="config.thinking_config",
                    supported_values=("minimal", "low", "medium", "high", "xhigh"),
                ),
                "safety_settings": CapabilityDetail(
                    status="passthrough", native_name="config.safety_settings"
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
            constraints=(
                ()
                if structured_with_tools
                else (
                    CapabilityConstraint(
                        name="gemini_structured_output_hosted_tools_requires_gemini_3",
                        status="unsupported",
                        reason=(
                            "Gemini structured output with built-in tools is available only "
                            "on Gemini 3 series models."
                        ),
                        hosted_tools_any=(
                            "web_search",
                            "file_search",
                            "code_interpreter",
                            "url_context",
                            "computer_use",
                            "raw",
                        ),
                        output_strategies_any=("provider_native",),
                    ),
                    CapabilityConstraint(
                        name="gemini_structured_output_function_tools_requires_gemini_3",
                        status="unsupported",
                        reason=(
                            "Gemini structured output with function calling is available only "
                            "on Gemini 3 series models."
                        ),
                        output_strategies_any=("provider_native",),
                        has_function_tools=True,
                    ),
                )
            ),
            summary=summary,
        )

    def _get_client(self) -> Any:
        """Return the configured Gemini client, creating one from credentials if needed."""
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

    async def create_cache(
        self,
        *,
        model: str,
        contents: Any,
        key: str | None = None,
        system_instruction: str | None = None,
        ttl: str | None = None,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ProviderCacheRecord:
        """Create Gemini cached content and return the runtime cache record."""
        client = self._get_client()
        caches = getattr(client, "caches", None)
        create = getattr(caches, "create", None)
        if not callable(create):
            raise UnsupportedFeatureError(
                "Gemini provider cache creation requires a client with caches.create(...)."
            )
        config = dict(kwargs.pop("config", {}))
        config.setdefault("contents", contents)
        if system_instruction is not None:
            config.setdefault("system_instruction", system_instruction)
        if ttl is not None:
            config.setdefault("ttl", ttl)
        if display_name is not None:
            config.setdefault("display_name", display_name)
        result = create(model=model, config=config, **kwargs)
        cache = await result if inspect.isawaitable(result) else result
        name = _attr(cache, "name")
        if not isinstance(name, str) or not name:
            raise ProviderExecutionError("Gemini caches.create(...) did not return a cache name.")
        expires_at = _parse_datetime(_attr(cache, "expire_time"))
        return ProviderCacheRecord(
            provider=self.provider_id,
            model=model,
            key=key,
            provider_cache_id=name,
            strategy="provider_managed",
            ttl=ttl,
            expires_at=expires_at,
            metadata={
                **(metadata or {}),
                "display_name": display_name,
                "provider_usage_metadata": _plain_mapping(
                    _attr(cache, "usage_metadata")
                ),
            },
        )

    async def delete_cache(self, provider_cache_id: str) -> None:
        client = self._get_client()
        caches = getattr(client, "caches", None)
        delete = getattr(caches, "delete", None)
        if not callable(delete):
            raise UnsupportedFeatureError(
                "Gemini provider cache deletion requires a client with caches.delete(...)."
            )
        result = delete(name=provider_cache_id)
        if inspect.isawaitable(result):
            await result

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        """Stream one Gemini GenerateContent turn as normalized events and provider state."""
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
        server_tool_ids: list[dict[str, Any]] = []
        source_references: list[dict[str, Any]] = []
        file_handles: list[dict[str, Any]] = []
        usage = None

        attempt = 0
        while True:
            emitted_provider_output = False
            assistant_parts.clear()
            thought_signatures.clear()
            tool_call_ids.clear()
            server_tool_ids.clear()
            source_references.clear()
            file_handles.clear()
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
                        server_tool_ids=server_tool_ids,
                        source_references=source_references,
                        file_handles=file_handles,
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
            server_tool_ids=server_tool_ids,
            source_references=source_references,
            file_handles=file_handles,
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
        """Translate a runtime turn request into Gemini generate_content_stream kwargs."""
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
            converted_tools = _convert_tools(tools)
            config["tools"] = converted_tools
            if (
                _gemini_supports_structured_output_with_tools(request.model)
                and _has_builtin_and_function_tools(converted_tools)
            ):
                config.setdefault("include_server_side_tool_invocations", True)
        controls = request.controls
        if controls.instructions is not None:
            config.setdefault("system_instruction", controls.instructions)
        if controls.temperature is not None:
            config.setdefault("temperature", controls.temperature)
        if controls.top_p is not None:
            config.setdefault("top_p", controls.top_p)
        if controls.max_output_tokens is not None:
            config.setdefault("max_output_tokens", controls.max_output_tokens)
        if controls.tool_choice is not None and "tool_config" not in config:
            config["tool_config"] = {
                "function_calling_config": _gemini_function_calling_config(
                    controls.tool_choice
                )
            }
        if controls.reasoning_effort is not None and "thinking_config" not in config:
            config["thinking_config"] = _gemini_thinking_config(
                request.model, controls.reasoning_effort
            )
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


_GEMINI_THINKING_BUDGETS = {
    "minimal": 0,
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
}


def _gemini_supports_structured_output_with_tools(model: str | None) -> bool:
    return "gemini-3" in (model or "").lower()


def _gemini_thinking_config(model: str, effort: str) -> dict[str, Any]:
    normalized_effort = effort.lower()
    if normalized_effort not in _GEMINI_THINKING_BUDGETS:
        raise UnsupportedFeatureError(
            f"Unsupported Gemini reasoning_effort={effort!r}; expected one of "
            f"{tuple(_GEMINI_THINKING_BUDGETS)}."
        )
    if "gemini-3" in model.lower():
        level = "high" if normalized_effort == "xhigh" else normalized_effort
        return {"thinking_level": level}
    return {"thinking_budget": _GEMINI_THINKING_BUDGETS[normalized_effort]}


def _gemini_function_calling_config(tool_choice: Any) -> dict[str, Any]:
    if isinstance(tool_choice, str):
        value = tool_choice.lower()
        if value == "auto":
            return {"mode": "AUTO"}
        if value in {"required", "any"}:
            return {"mode": "ANY"}
        if value == "none":
            return {"mode": "NONE"}
        if value == "validated":
            return {"mode": "VALIDATED"}
    if isinstance(tool_choice, dict):
        if "function_calling_config" in tool_choice:
            config = tool_choice["function_calling_config"]
            if isinstance(config, dict):
                return dict(config)
        if "mode" in tool_choice:
            config = {"mode": str(tool_choice["mode"]).upper()}
            allowed = tool_choice.get("allowed_function_names")
            if allowed is not None:
                config["allowed_function_names"] = list(allowed)
            return config
        name = tool_choice.get("name")
        function = tool_choice.get("function")
        if name is None and isinstance(function, dict):
            name = function.get("name")
        if tool_choice.get("type") == "function" and isinstance(name, str):
            return {"mode": "ANY", "allowed_function_names": [name]}
    raise UnsupportedFeatureError(
        "Gemini tool_choice must be auto, required/any, none, validated, or a "
        "specific function tool choice."
    )


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
                "parameters": _gemini_function_schema(
                    tool.get("parameters") or {"type": "object", "properties": {}}
                ),
            }
        )
    if declarations:
        passthrough.insert(0, {"function_declarations": declarations})
    return passthrough


def _has_builtin_and_function_tools(tools: list[dict[str, Any]]) -> bool:
    has_function = any(
        isinstance(tool, dict)
        and (
            "function_declarations" in tool
            or "functionDeclarations" in tool
        )
        for tool in tools
    )
    has_builtin = any(
        isinstance(tool, dict)
        and any(
            key in tool
            for key in (
                "google_search",
                "googleSearch",
                "google_maps",
                "googleMaps",
                "code_execution",
                "codeExecution",
                "url_context",
                "urlContext",
                "file_search",
                "fileSearch",
                "computer_use",
                "computerUse",
            )
        )
        for tool in tools
    )
    return has_function and has_builtin


def _gemini_function_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_gemini_function_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: _gemini_function_schema(value)
        for key, value in schema.items()
        if key != "additionalProperties"
    }


def _map_chunk(
    chunk: Any,
    *,
    provider: str,
    assistant_parts: list[dict[str, Any]],
    thought_signatures: list[Any],
    tool_call_ids: list[str],
    server_tool_ids: list[dict[str, Any]],
    source_references: list[dict[str, Any]],
    file_handles: list[dict[str, Any]],
) -> list[AgentEvent]:
    """Map a Gemini stream chunk into runtime events while collecting replay state."""
    events: list[AgentEvent] = []
    for candidate in _attr(chunk, "candidates") or []:
        source_references.extend(_candidate_source_references(candidate))
        content = _attr(candidate, "content")
        for index, part in enumerate(_attr(content, "parts") or []):
            part_dict = _part_to_dict(part)
            assistant_parts.append(part_dict)
            signature = _attr(part, "thought_signature")
            if signature is not None:
                thought_signatures.append(signature)
            file_handles.extend(_part_file_handles(part_dict))

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

            tool_call = _attr(part, "tool_call")
            if tool_call is not None:
                call_id = _attr(tool_call, "id") or f"gemini_tool_{len(server_tool_ids) + 1}"
                tool_type = _attr(tool_call, "tool_type") or "server_tool"
                server_tool_ids.append(
                    {
                        "id": call_id,
                        "tool_type": tool_type,
                    }
                )
                item = RunItem(
                    type=ItemTypes.HOSTED_TOOL_CALL,
                    provider=provider,
                    status="completed",
                    id=call_id,
                    data={
                        "call_id": call_id,
                        "hosted_tool_type": tool_type,
                        "provider_item_type": "tool_call",
                    },
                    raw=tool_call,
                )
                events.append(
                    AgentEvent(
                        type=EventTypes.HOSTED_TOOL_CALL_COMPLETED,
                        provider=provider,
                        item_id=item.id,
                        data={**item.data, "item": item, "index": index},
                        raw=part,
                    )
                )
                continue

            tool_response = _attr(part, "tool_response")
            if tool_response is not None:
                response_id = _attr(tool_response, "id") or f"gemini_tool_result_{len(server_tool_ids) + 1}"
                tool_type = _attr(tool_response, "tool_type") or "server_tool"
                server_tool_ids.append(
                    {
                        "id": response_id,
                        "tool_type": tool_type,
                        "result": True,
                    }
                )
                item = RunItem(
                    type=ItemTypes.HOSTED_TOOL_RESULT,
                    provider=provider,
                    status="completed",
                    id=response_id,
                    data={
                        "call_id": response_id,
                        "hosted_tool_type": tool_type,
                        "provider_item_type": "tool_response",
                    },
                    raw=tool_response,
                )
                events.append(
                    AgentEvent(
                        type=EventTypes.HOSTED_TOOL_CALL_COMPLETED,
                        provider=provider,
                        item_id=item.id,
                        data={**item.data, "item": item, "index": index},
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
    server_tool_ids: list[dict[str, Any]],
    source_references: list[dict[str, Any]],
    file_handles: list[dict[str, Any]],
) -> ProviderState:
    """Build Gemini replay state from emitted assistant parts and provider identifiers."""
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
        tool_state=_gemini_tool_state(
            tool_call_ids=tool_call_ids,
            server_tool_ids=server_tool_ids,
            source_references=source_references,
            file_handles=file_handles,
        ),
        continuation={"response_id": response_id} if response_id else {},
    )


def _gemini_tool_state(
    *,
    tool_call_ids: list[str],
    server_tool_ids: list[dict[str, Any]],
    source_references: list[dict[str, Any]],
    file_handles: list[dict[str, Any]],
) -> dict[str, Any]:
    state: dict[str, Any] = {}
    if tool_call_ids:
        state["tool_call_ids"] = list(tool_call_ids)
    if server_tool_ids:
        state["server_tool_ids"] = _dedupe_dicts(server_tool_ids)
    if source_references:
        state["source_references"] = _dedupe_dicts(source_references)
    if file_handles:
        state["file_handles"] = _dedupe_dicts(file_handles)
    return state


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
    tool_call = _attr(part, "tool_call")
    if tool_call is not None:
        out["tool_call"] = {
            "id": _attr(tool_call, "id"),
            "tool_type": _attr(tool_call, "tool_type"),
        }
    tool_response = _attr(part, "tool_response")
    if tool_response is not None:
        out["tool_response"] = {
            "id": _attr(tool_response, "id"),
            "tool_type": _attr(tool_response, "tool_type"),
        }
    file_data = _attr(part, "file_data")
    if file_data is not None:
        out["file_data"] = _plain_mapping(file_data)
    return out


def _candidate_source_references(candidate: Any) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for name in (
        "grounding_metadata",
        "groundingMetadata",
        "url_context_metadata",
        "urlContextMetadata",
        "citation_metadata",
        "citationMetadata",
    ):
        value = _attr(candidate, name)
        if value is not None:
            references.append({"field": name, "value": _plain_mapping(value)})
    return references


def _part_file_handles(part: dict[str, Any]) -> list[dict[str, Any]]:
    handles: list[dict[str, Any]] = []
    file_data = part.get("file_data")
    if isinstance(file_data, dict):
        handles.append({"field": "file_data", "value": file_data})
    for key in ("file_uri", "fileUri", "file_id", "fileId"):
        value = part.get(key)
        if value is not None:
            handles.append({"field": key, "value": value})
    return handles


def _dedupe_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for value in values:
        key = repr(sorted(value.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _plain_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump(exclude_none=True)
        return dumped if isinstance(dumped, dict) else {"value": dumped}
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {"value": repr(value)}


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
