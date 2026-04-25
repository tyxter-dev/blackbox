"""Provider-native adapter for the Gemini GenerateContent API.

Maps streamed ``GenerateContentResponse`` chunks into canonical runtime
events. The adapter preserves Gemini-shaped content/part dictionaries in
``ProviderState.native_history`` rather than projecting them into chat
messages.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agent_runtime.core.accounting import add_usage, usage_from_gemini_chunk
from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.errors import ProviderExecutionError, ProviderNotConfiguredError
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.providers.base import TurnRequest


class GeminiGenerateContentProvider:
    """Provider-native adapter for Google's Gemini GenerateContent API."""

    provider_id = "google"

    def __init__(self, *, api_key: str | None = None, client: Any | None = None) -> None:
        self.api_key = api_key
        self._client = client

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            supports_streaming_events=True,
            supports_function_tools=True,
            supports_parallel_tool_calls=True,
            supports_hosted_tools=True,
            supports_remote_mcp=False,
            supports_reasoning_items=True,
            supports_provider_state=True,
            supports_structured_output=False,
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise ProviderNotConfiguredError(
                "GeminiGenerateContentProvider requires either a client or an api_key."
            )
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "GeminiGenerateContentProvider needs the 'google-genai' package; "
                "install with the [google] extra."
            ) from exc
        self._client = genai.Client(api_key=self.api_key)
        return self._client

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

        try:
            async for chunk in client.aio.models.generate_content_stream(**kwargs):
                response_id = _attr(chunk, "response_id") or response_id
                usage = add_usage(usage, usage_from_gemini_chunk(chunk))
                for event in _map_chunk(
                    chunk,
                    provider=self.provider_id,
                    assistant_parts=assistant_parts,
                    thought_signatures=thought_signatures,
                    tool_call_ids=tool_call_ids,
                ):
                    yield event
        except ProviderExecutionError:
            raise
        except Exception as exc:
            raise ProviderExecutionError(
                f"Gemini GenerateContent stream failed: {exc!s}"
            ) from exc

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
        tools = request.tools or config.get("tools")
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
        if config:
            kwargs["config"] = config
        kwargs.update(extra)
        return kwargs


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
        else:
            passthrough.append(entry)

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
