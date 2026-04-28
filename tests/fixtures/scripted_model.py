from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.core.capabilities import (
    CapabilityDetail,
    HostedToolSupport,
    ModelCapabilities,
    ModelCapabilityProfile,
)
from agent_runtime.core.events import AgentEvent, EventTypes
from agent_runtime.core.items import ItemTypes, RunItem
from agent_runtime.core.state import ProviderState
from agent_runtime.providers.base import TurnRequest

TurnScript = Callable[[TurnRequest], Iterable[AgentEvent]]


@dataclass
class ScriptedModelProvider:
    """Test-only model provider that emits a pre-scripted event sequence per turn.

    Each registered script receives the incoming TurnRequest and yields AgentEvents.
    Useful for driving the local agent's tool/approval loop without real network calls.
    """

    provider_id: str = "scripted"
    scripts: list[TurnScript] = field(default_factory=list)
    calls: list[TurnRequest] = field(default_factory=list)

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(
            supports_streaming_events=True,
            supports_function_tools=True,
            supports_hosted_tools=True,
            supports_provider_state=True,
            hosted_tools={
                "web_search": HostedToolSupport("web_search", True, True, True, False),
                "shell": HostedToolSupport("shell", True, True, False, True),
                "apply_patch": HostedToolSupport("apply_patch", True, True, False, True),
                "computer": HostedToolSupport("computer", True, True, False, True),
            },
        )

    def capability_profile(self, model: str | None = None) -> ModelCapabilityProfile:
        """Return a permissive test profile that mirrors the scripted provider capabilities."""
        summary = self.capabilities(model)
        return ModelCapabilityProfile(
            provider=self.provider_id,
            model=model,
            hosted_tools={
                "web_search": CapabilityDetail(status="supported"),
                "bash": CapabilityDetail(status="supported"),
                "shell": CapabilityDetail(status="supported"),
                "apply_patch": CapabilityDetail(status="supported"),
                "computer_use": CapabilityDetail(status="supported"),
                "raw": CapabilityDetail(status="passthrough"),
            },
            output_strategies={
                "provider_native": CapabilityDetail(
                    status="supported" if summary.supports_structured_output else "unsupported"
                ),
                "finalizer_tool": CapabilityDetail(status="supported"),
                "posthoc_parse": CapabilityDetail(status="supported"),
                "posthoc_parse_with_retry": CapabilityDetail(status="supported"),
            },
            controls={
                "instructions": CapabilityDetail(status="conditional"),
                "temperature": CapabilityDetail(status="conditional"),
                "top_p": CapabilityDetail(status="conditional"),
                "max_output_tokens": CapabilityDetail(status="conditional"),
                "tool_choice": CapabilityDetail(status="conditional"),
                "parallel_tool_calls": CapabilityDetail(status="conditional"),
                "reasoning_effort": CapabilityDetail(status="conditional"),
                "cache": CapabilityDetail(status="conditional"),
                "extra": CapabilityDetail(status="passthrough"),
            },
            state_modes={
                "none": CapabilityDetail(status="supported"),
                "provider_stateful": CapabilityDetail(status="supported"),
                "stateless_replay": CapabilityDetail(status="supported"),
                "zdr": CapabilityDetail(status="unsupported"),
                "encrypted_reasoning": CapabilityDetail(status="unsupported"),
            },
            summary=summary,
        )

    def queue(self, script: TurnScript) -> None:
        self.scripts.append(script)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        self.calls.append(request)
        if not self.scripts:
            raise AssertionError("ScriptedModelProvider received a turn but has no scripts queued")
        script = self.scripts.pop(0)
        for event in script(request):
            yield event


def text_only_turn(text: str) -> TurnScript:
    def script(request: TurnRequest) -> Iterable[AgentEvent]:
        yield AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            provider="scripted",
            data={"model": request.model},
        )
        item = RunItem(type=ItemTypes.MESSAGE, provider="scripted", status="completed",
                       data={"role": "assistant", "text": text})
        yield AgentEvent(type=EventTypes.MODEL_TEXT_DELTA, provider="scripted",
                         item_id=item.id, data={"delta": text})
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            item_id=item.id,
            data={"provider_state": ProviderState(provider="scripted", previous_response_id=item.id)},
        )
    return script


def tool_call_turn(*, call_id: str, name: str, arguments: dict[str, Any]) -> TurnScript:
    def script(request: TurnRequest) -> Iterable[AgentEvent]:
        yield AgentEvent(
            type=EventTypes.MODEL_REQUEST_STARTED,
            provider="scripted",
            data={"model": request.model},
        )
        yield AgentEvent(
            type=EventTypes.TOOL_CALL_REQUESTED,
            provider="scripted",
            item_id=call_id,
            data={"call_id": call_id, "name": name, "arguments": arguments},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider="scripted",
            data={"provider_state": ProviderState(provider="scripted", previous_response_id=call_id)},
        )
    return script
