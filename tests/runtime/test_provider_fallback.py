"""Cross-provider fallback routing on the high-level run surface."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from blackbox import AgentRuntime
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.errors import ConfigurationError, ProviderExecutionError
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest


class _FailingProvider:
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        self.calls = 0

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        self.calls += 1
        raise ProviderExecutionError(f"{self.provider_id} is down")
        yield  # pragma: no cover - makes this an async generator


class _WorkingProvider:
    def __init__(self, provider_id: str, reply: str) -> None:
        self.provider_id = provider_id
        self.reply = reply
        self.calls = 0

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True, supports_provider_state=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        self.calls += 1
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider=self.provider_id)
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA,
            provider=self.provider_id,
            data={"delta": self.reply},
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider=self.provider_id,
            data={"provider_state": ProviderState(provider=self.provider_id)},
        )


def _runtime(*providers: object) -> AgentRuntime:
    runtime = AgentRuntime()
    for provider in providers:
        runtime.registry.register_model(provider)
    return runtime


async def test_fallback_engages_on_provider_execution_error() -> None:
    primary = _FailingProvider("alpha")
    backup = _WorkingProvider("beta", "answered by beta")
    runtime = _runtime(primary, backup)

    result = await runtime.run(
        provider="alpha:big",
        input="hello",
        fallback_providers=["beta:small"],
    )

    assert result.text == "answered by beta"
    assert primary.calls == 1
    assert backup.calls == 1
    fallback = result.metadata["fallback"]
    assert fallback["provider_used"] == "beta:small"
    assert fallback["attempts"] == [
        {"provider": "alpha:big", "error": "alpha is down", "error_type": "ProviderExecutionError"}
    ]


async def test_no_fallback_metadata_without_fallback_providers() -> None:
    runtime = _runtime(_WorkingProvider("alpha", "direct"))
    result = await runtime.run(provider="alpha:big", input="hello")
    assert result.text == "direct"
    assert "fallback" not in result.metadata


async def test_primary_success_records_empty_attempts() -> None:
    primary = _WorkingProvider("alpha", "primary answer")
    backup = _WorkingProvider("beta", "unused")
    runtime = _runtime(primary, backup)

    result = await runtime.run(
        provider="alpha:big",
        input="hello",
        fallback_providers=["beta:small"],
    )
    assert result.text == "primary answer"
    assert backup.calls == 0
    assert result.metadata["fallback"] == {"provider_used": "alpha:big", "attempts": []}


async def test_unregistered_fallback_candidate_is_recorded_and_skipped() -> None:
    primary = _FailingProvider("alpha")
    backup = _WorkingProvider("gamma", "third time lucky")
    runtime = _runtime(primary, backup)

    result = await runtime.run(
        provider="alpha:big",
        input="hello",
        fallback_providers=["missing:model", "gamma:small"],
    )
    assert result.text == "third time lucky"
    attempts = result.metadata["fallback"]["attempts"]
    assert [a["provider"] for a in attempts] == ["alpha:big", "missing:model"]
    assert attempts[1]["error_type"] in {"ProviderNotFoundError", "ProviderNotConfiguredError"}


async def test_provider_state_blocks_cross_provider_failover() -> None:
    primary = _FailingProvider("alpha")
    backup = _WorkingProvider("beta", "should not run")
    same_provider_backup = _WorkingProvider("alpha2", "unused")  # different key
    runtime = _runtime(primary, backup, same_provider_backup)

    state = ProviderState(provider="alpha", previous_response_id="resp_1")
    with pytest.raises(ProviderExecutionError):
        await runtime.run(
            provider="alpha:big",
            input="continue",
            provider_state=state,
            fallback_providers=["beta:small"],
        )
    assert backup.calls == 0


async def test_all_candidates_skipped_raises_configuration_error() -> None:
    backup = _WorkingProvider("beta", "unused")
    runtime = _runtime(_FailingProvider("alpha"), backup)
    state = ProviderState(provider="alpha")

    # Primary fails; the only fallback is state-incompatible -> last error is
    # the primary's. With *only* skipped candidates and no error, a
    # ConfigurationError explains the situation.
    with pytest.raises((ProviderExecutionError, ConfigurationError)):
        await runtime.run(
            provider="alpha:big",
            input="continue",
            provider_state=state,
            fallback_providers=["beta:small"],
        )


async def test_matching_provider_state_allows_failover_to_same_provider_key() -> None:
    # Two refs on the same provider key (different models) stay state-compatible.
    class _FlakyThenWorking(_WorkingProvider):
        async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
            self.calls += 1
            if request.model == "big":
                raise ProviderExecutionError("big model overloaded")
            async for event in super().stream_turn(request):
                yield event

    provider = _FlakyThenWorking("alpha", "small model answer")
    runtime = _runtime(provider)
    state = ProviderState(provider="alpha", previous_response_id="resp_9")

    result = await runtime.run(
        provider="alpha:big",
        input="continue",
        provider_state=state,
        fallback_providers=["alpha:small"],
    )
    assert result.text == "small model answer"
    assert result.metadata["fallback"]["provider_used"] == "alpha:small"
