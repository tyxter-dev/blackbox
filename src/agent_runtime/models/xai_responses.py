from __future__ import annotations

from typing import Any

from agent_runtime.core.capabilities import ModelCapabilities
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
            api_key=api_key,
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

    @staticmethod
    def _build_request_kwargs(request: TurnRequest) -> dict[str, Any]:
        for hosted_tool in request.hosted_tools:
            to_raw_hosted_tool(hosted_tool, provider="xAI")
        kwargs = OpenAIResponsesProvider._build_request_kwargs(request)
        kwargs.pop("instructions", None)
        return kwargs
