from __future__ import annotations

import os
from typing import Any

from agent_runtime.core.capabilities import (
    CapabilityDetail,
    ModelCapabilities,
    ModelCapabilityProfile,
)
from agent_runtime.hosted_tools import to_raw_hosted_tool
from agent_runtime.models.openai_responses import OpenAIResponsesProvider
from agent_runtime.providers.base import TurnRequest


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
            supports_structured_output=False,
        )

    def capability_profile(self, model: str | None = None) -> ModelCapabilityProfile:
        summary = self.capabilities(model)
        return ModelCapabilityProfile(
            provider=self.provider_id,
            model=model,
            hosted_tools={
                "raw": CapabilityDetail(status="passthrough"),
                "web_search": CapabilityDetail(status="unsupported"),
                "file_search": CapabilityDetail(status="unsupported"),
                "code_interpreter": CapabilityDetail(status="unsupported"),
                "remote_mcp": CapabilityDetail(status="unsupported"),
                "tool_search": CapabilityDetail(status="unsupported"),
                "computer_use": CapabilityDetail(status="unsupported"),
                "image_generation": CapabilityDetail(status="unsupported"),
            },
            output_strategies={
                "provider_native": CapabilityDetail(status="unsupported"),
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
            to_raw_hosted_tool(hosted_tool, provider="xAI")
        kwargs = OpenAIResponsesProvider._build_request_kwargs(request)
        kwargs.pop("instructions", None)
        return kwargs
