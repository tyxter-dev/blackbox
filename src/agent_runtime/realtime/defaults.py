from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agent_runtime.providers.registry import ProviderRegistry
from agent_runtime.realtime.fake import FakeRealtimeProvider
from agent_runtime.realtime.providers import GeminiLiveProvider, OpenAIRealtimeProvider


def register_default_realtime_providers(
    target: ProviderRegistry | Any,
    *,
    include: Iterable[str] | None = None,
) -> ProviderRegistry:
    registry = target.registry if hasattr(target, "registry") else target
    if not isinstance(registry, ProviderRegistry):
        raise TypeError("target must be a ProviderRegistry or object with a registry attribute.")
    selected = set(include or {"fake-realtime", "openai-realtime", "gemini-live"})
    if "fake-realtime" in selected or "fake" in selected:
        registry.register_realtime(FakeRealtimeProvider())
    if "openai-realtime" in selected or "openai" in selected:
        registry.register_realtime(OpenAIRealtimeProvider())
    if "gemini-live" in selected or "gemini" in selected:
        registry.register_realtime(GeminiLiveProvider())
    return registry
