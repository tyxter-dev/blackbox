from __future__ import annotations

from collections.abc import AsyncIterator

from blackbox.core.capabilities import (
    CapabilityDetail,
    ModelCapabilities,
    ModelCapabilityProfile,
)
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.items import ItemTypes, RunItem
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest


class EchoModelProvider:
    """Tiny test provider that streams the input back as text.

    This gives the scaffold a dependency-free provider for tests and examples.
    """

    provider_id = "echo"

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True, supports_provider_state=True)

    def capability_profile(self, model: str | None = None) -> ModelCapabilityProfile:
        summary = self.capabilities(model)
        return ModelCapabilityProfile(
            provider=self.provider_id,
            model=model,
            hosted_tools={
                "raw": CapabilityDetail(status="unsupported"),
            },
            output_strategies={
                "posthoc_parse": CapabilityDetail(status="supported"),
                "posthoc_parse_with_retry": CapabilityDetail(status="supported"),
                "finalizer_tool": CapabilityDetail(status="unsupported"),
                "provider_native": CapabilityDetail(status="unsupported"),
            },
            controls={
                "extra": CapabilityDetail(status="unsupported"),
            },
            state_modes={
                "none": CapabilityDetail(status="supported"),
                "provider_stateful": CapabilityDetail(
                    status="supported",
                    reason="Echo stores a simple synthetic turn counter in ProviderState.",
                ),
                "stateless_replay": CapabilityDetail(status="unsupported"),
                "zdr": CapabilityDetail(status="unsupported"),
                "encrypted_reasoning": CapabilityDetail(status="unsupported"),
            },
            summary=summary,
        )

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
