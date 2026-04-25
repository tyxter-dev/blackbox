from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.errors import ProviderNotConfiguredError
from agent_runtime.core.events import AgentEvent
from agent_runtime.providers.base import TurnRequest


class AnthropicMessagesProvider:
    """Scaffold for a provider-native Anthropic Messages adapter."""

    provider_id = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            supports_streaming_events=True,
            supports_function_tools=True,
            supports_parallel_tool_calls=False,
            supports_hosted_tools=True,
            supports_remote_mcp=True,
            supports_reasoning_items=True,
            supports_provider_state=True,
            supports_structured_output=True,
        )

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        if not self.api_key:
            raise ProviderNotConfiguredError("AnthropicMessagesProvider requires an api_key.")
        raise NotImplementedError("Anthropic Messages adapter scaffold only.")
        yield  # pragma: no cover
