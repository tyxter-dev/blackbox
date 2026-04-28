from __future__ import annotations

from agent_runtime import AgentRuntime
from agent_runtime.core.state import ProviderState
from agent_runtime.providers.model_adapters.echo import EchoModelProvider


async def test_run_returns_provider_state() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    result = await runtime.models.run(provider="echo/echo-mini", input="first")

    assert result.provider_state is not None
    assert result.provider_state.provider == "echo"
    assert result.provider_state.continuation == {"turn": 1}


async def test_run_accepts_provider_state_for_continuation() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(EchoModelProvider())

    first = await runtime.models.run(provider="echo/echo-mini", input="hi")
    second = await runtime.models.run(
        provider="echo/echo-mini",
        input="again",
        provider_state=first.provider_state,
    )

    assert second.provider_state is not None
    assert second.provider_state.continuation == {"turn": 2}


async def test_provider_state_defaults_are_safe() -> None:
    state = ProviderState(provider="x")
    assert state.native_history == []
    assert state.reasoning_state == {}
    assert state.tool_state == {}
    assert state.continuation == {}
