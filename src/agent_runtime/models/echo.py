from __future__ import annotations

from collections.abc import AsyncIterator

from agent_runtime.core.capabilities import ModelCapabilities
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.providers.base import TurnRequest


class EchoModelProvider:
    """Tiny test provider that streams the input back as text.

    This gives the scaffold a dependency-free provider for tests and examples.
    """

    provider_id = "echo"

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True, supports_provider_state=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        text = request.input if isinstance(request.input, str) else str(request.input)
        prior = request.provider_state
        turn_index = (prior.continuation.get("turn", 0) + 1) if prior else 1
        yield AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            provider=self.provider_id,
            data={"model": request.model, "turn": turn_index},
        )
        item = RunItem(
            type=ItemTypes.MESSAGE,
            provider=self.provider_id,
            status="completed",
            data={"role": "assistant", "text": text},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_ITEM_CREATED,
            provider=self.provider_id,
            item_id=item.id,
            data={"item": item},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA,
            provider=self.provider_id,
            item_id=item.id,
            data={"delta": text},
        )
        provider_state = ProviderState(
            provider=self.provider_id,
            previous_response_id=item.id,
            continuation={"turn": turn_index},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider=self.provider_id,
            item_id=item.id,
            data={"model": request.model, "provider_state": provider_state},
        )
