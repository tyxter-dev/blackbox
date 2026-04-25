from __future__ import annotations

from typing import Any

from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.models.openai_responses import OpenAIResponsesProvider


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
    ) -> None:
        super().__init__(
            api_key=api_key,
            client=client,
            base_url=base_url or self.default_base_url,
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
