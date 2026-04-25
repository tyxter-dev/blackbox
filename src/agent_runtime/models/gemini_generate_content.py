from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.errors import ProviderNotConfiguredError, UnsupportedFeatureError
from agent_runtime.core.events import AgentEvent
from agent_runtime.providers.base import TurnRequest


class GeminiGenerateContentProvider:
    """Scaffold for a provider-native Gemini GenerateContent adapter.

    TODO: preserve native Content/Part objects, function-call IDs, part ordering,
    and thought signatures in ProviderState.
    """

    provider_id = "google"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities()

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        if not self.api_key:
            raise ProviderNotConfiguredError("GeminiGenerateContentProvider requires an api_key.")
        raise UnsupportedFeatureError("Gemini GenerateContent adapter scaffold only.")
        yield  # pragma: no cover
