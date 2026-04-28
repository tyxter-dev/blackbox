from __future__ import annotations

import os
from typing import Any

from agent_runtime.core.capabilities import (
    CapabilityDetail,
    HostedToolSupport,
    ModelCapabilities,
    ModelCapabilityProfile,
)
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from agent_runtime.providers.base import TurnRequest
from agent_runtime.tools.hosted.specs import HostedToolRaw, WebSearch


class XAIResponsesProvider(OpenAIResponsesProvider):
    """Provider-native adapter for xAI's OpenAI-compatible Responses API."""

    provider_id = "xai"
    default_base_url = "https://api.x.ai/v1"

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
        super().__init__(
            api_key=api_key or os.environ.get("XAI_API_KEY"),
            client=client,
            base_url=base_url or self.default_base_url,
            timeout=timeout,
            max_retries=max_retries,
            retry_min_delay=retry_min_delay,
        )

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            supports_streaming_events=True,
            supports_function_tools=True,
            supports_parallel_tool_calls=True,
            supports_hosted_tools=True,
            supports_tool_search=False,
            supports_client_executed_tool_search=False,
            supports_remote_mcp=False,
            supports_reasoning_items=True,
            supports_provider_state=True,
            supports_structured_output=True,
            hosted_tools={
                "web_search": HostedToolSupport("web_search", True, True, True, False),
            },
        )

    def capability_profile(self, model: str | None = None) -> ModelCapabilityProfile:
        summary = self.capabilities(model)
        return ModelCapabilityProfile(
            provider=self.provider_id,
            model=model,
            hosted_tools={
                "raw": CapabilityDetail(status="passthrough"),
                "web_search": CapabilityDetail(status="supported", native_name="web_search"),
                "file_search": CapabilityDetail(status="unsupported"),
                "code_interpreter": CapabilityDetail(status="unsupported"),
                "remote_mcp": CapabilityDetail(status="unsupported"),
                "tool_search": CapabilityDetail(status="unsupported"),
                "computer_use": CapabilityDetail(status="unsupported"),
                "image_generation": CapabilityDetail(status="unsupported"),
            },
            output_strategies={
                "provider_native": CapabilityDetail(
                    status="supported", native_name="text.format"
                ),
                "finalizer_tool": CapabilityDetail(status="supported"),
                "posthoc_parse": CapabilityDetail(status="supported"),
                "posthoc_parse_with_retry": CapabilityDetail(status="supported"),
            },
            controls={
                "instructions": CapabilityDetail(
                    status="unsupported",
                    reason="xAI Responses adapter strips instructions before request dispatch.",
                ),
                "temperature": CapabilityDetail(status="supported", native_name="temperature"),
                "top_p": CapabilityDetail(status="supported", native_name="top_p"),
                "max_output_tokens": CapabilityDetail(
                    status="supported", native_name="max_output_tokens"
                ),
                "tool_choice": CapabilityDetail(status="supported", native_name="tool_choice"),
                "parallel_tool_calls": CapabilityDetail(
                    status="supported", native_name="parallel_tool_calls"
                ),
                "reasoning_effort": CapabilityDetail(status="conditional"),
                "tool_search": CapabilityDetail(status="unsupported"),
                "compaction": CapabilityDetail(status="unsupported"),
                "modalities": CapabilityDetail(status="unsupported"),
                "extra": CapabilityDetail(status="passthrough"),
            },
            state_modes={
                "none": CapabilityDetail(status="supported"),
                "provider_stateful": CapabilityDetail(
                    status="conditional", native_name="previous_response_id"
                ),
                "stateless_replay": CapabilityDetail(status="conditional"),
                "zdr": CapabilityDetail(status="unsupported"),
                "encrypted_reasoning": CapabilityDetail(status="unsupported"),
            },
            summary=summary,
        )

    @staticmethod
    def _build_request_kwargs(request: TurnRequest) -> dict[str, Any]:
        for hosted_tool in request.hosted_tools:
            _validate_xai_hosted_tool(hosted_tool)
        kwargs = OpenAIResponsesProvider._build_request_kwargs(request)
        kwargs.pop("instructions", None)
        if "tools" in kwargs:
            kwargs["tools"] = [_sanitize_xai_tool(tool) for tool in kwargs["tools"]]
        return kwargs


def _validate_xai_hosted_tool(hosted_tool: Any) -> None:
    if isinstance(hosted_tool, (HostedToolRaw, WebSearch)):
        return
    from agent_runtime.tools.hosted.specs import to_raw_hosted_tool

    to_raw_hosted_tool(hosted_tool, provider="xAI")


def _sanitize_xai_tool(tool: Any) -> Any:
    if not isinstance(tool, dict):
        return tool
    return _strip_xai_schema_strictness(tool)


def _strip_xai_schema_strictness(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_xai_schema_strictness(item) for item in value]
    if not isinstance(value, dict):
        return value
    sanitized: dict[str, Any] = {}
    for key, nested in value.items():
        if key == "strict" and nested is True:
            continue
        if key == "additionalProperties" and nested is False:
            continue
        sanitized[key] = _strip_xai_schema_strictness(nested)
    return sanitized
