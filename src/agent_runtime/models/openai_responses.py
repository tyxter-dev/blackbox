from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.errors import ProviderNotConfiguredError
from agent_runtime.core.events import AgentEvent
from agent_runtime.providers.base import TurnRequest


class OpenAIResponsesProvider:
    """Scaffold for a provider-native OpenAI Responses adapter.

    TODO: implement event mapping for Responses output items:
    - message
    - reasoning
    - function_call / function_call_output
    - web_search_call / file_search_call
    - tool_search_call / tool_search_output
    - mcp_list_tools / mcp_call / approval requests
    """

    provider_id = "openai"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

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

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        if not self.api_key:
            raise ProviderNotConfiguredError("OpenAIResponsesProvider requires an api_key.")
        # Implement with openai.responses.stream/create in the real adapter.
        raise NotImplementedError("OpenAI Responses adapter scaffold only.")
        yield  # pragma: no cover
