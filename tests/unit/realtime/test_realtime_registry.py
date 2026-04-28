from __future__ import annotations

import pytest

from agent_runtime.core.errors import ProviderNotFoundError
from agent_runtime.providers.model_adapters.echo import EchoModelProvider
from agent_runtime.providers.registry import ProviderRegistry
from agent_runtime.realtime.fake import FakeRealtimeProvider


def test_registry_keeps_realtime_separate_from_model_providers() -> None:
    registry = ProviderRegistry()
    model = EchoModelProvider()
    realtime = FakeRealtimeProvider()

    registry.register_model(model)
    registry.register_realtime(realtime)

    assert registry.get_model("echo/echo-mini") is model
    assert registry.get_realtime("fake-realtime:gpt-realtime") is realtime
    assert "fake-realtime" in registry.list_realtime_providers()
    with pytest.raises(ProviderNotFoundError):
        registry.get_model("fake-realtime:gpt-realtime")
    with pytest.raises(ProviderNotFoundError):
        registry.get_realtime("echo/echo-mini")
