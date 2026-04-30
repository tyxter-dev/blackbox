from __future__ import annotations

import pytest

from blackbox import AgentRuntime
from blackbox.core.errors import RealtimeUnsupportedFeatureError
from blackbox.realtime.fake import FakeRealtimeProvider
from blackbox.realtime.provider import RealtimeSessionConfig, TurnDetectionConfig


async def test_runtime_rejects_unsupported_transport_before_connect() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_realtime(FakeRealtimeProvider())

    with pytest.raises(RealtimeUnsupportedFeatureError):
        await runtime.realtime.connect(
            provider="fake-realtime:gpt-realtime",
            transport="sip",
        )


async def test_runtime_rejects_unsupported_modality_before_connect() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_realtime(FakeRealtimeProvider())

    with pytest.raises(RealtimeUnsupportedFeatureError):
        await runtime.realtime.connect(
            provider="fake-realtime:gpt-realtime",
            config=RealtimeSessionConfig(
                input_modalities=["text", "file"],
                turn_detection=TurnDetectionConfig(),
            ),
        )


async def test_disabled_tool_mode_rejects_registered_tools() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_realtime(FakeRealtimeProvider())
    runtime.tools.register(lambda text: text, name="echo")

    with pytest.raises(RealtimeUnsupportedFeatureError):
        await runtime.realtime.connect(
            provider="fake-realtime:gpt-realtime",
            tools=["echo"],
            tool_mode="disabled",
        )
