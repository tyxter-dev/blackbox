from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from blackbox.core.errors import ProviderNotFoundError
from blackbox.providers.base import AgentProvider, ModelProvider

if TYPE_CHECKING:
    from blackbox.realtime.provider import RealtimeProvider


@dataclass(slots=True, frozen=True)
class ProviderRef:
    """Parsed provider reference.

    Either ``:`` or ``/`` works as the separator. ``:`` is the canonical form
    at the high level (``model="openai:gpt-5.4"``) because ``/`` collides with
    namespaced agent paths such as ``vertex-agent-engine/projects/foo/agent``.

    Examples:
    - ``"openai:gpt-5"`` -> provider_key="openai", resource="gpt-5"
    - ``"openai/gpt-5"`` -> provider_key="openai", resource="gpt-5"
    - ``"vertex-agent-engine/projects/foo/agent"`` -> provider_key="vertex-agent-engine",
      resource="projects/foo/agent"
    - ``"echo"`` -> provider_key="echo", resource=None
    """

    provider_key: str
    resource: str | None = None

    @classmethod
    def parse(cls, value: str) -> ProviderRef:
        if ":" in value:
            head, _, tail = value.partition(":")
            return cls(provider_key=head, resource=tail or None)
        head, sep, tail = value.partition("/")
        return cls(provider_key=head, resource=tail if sep else None)


class ProviderRegistry:
    """Registry for model and agent providers.

    This is the multi-provider compatibility layer. It takes inspiration from
    provider routers without adopting a single normalized provider schema.
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelProvider] = {}
        self._agents: dict[str, AgentProvider] = {}
        self._realtime: dict[str, RealtimeProvider] = {}

    def register_model(self, provider: ModelProvider, *aliases: str) -> None:
        keys = (provider.provider_id, *aliases)
        for key in keys:
            self._models[key] = provider

    def register_agent(self, provider: AgentProvider, *aliases: str) -> None:
        provider_aliases = getattr(provider, "provider_aliases", ())
        keys = (provider.provider_id, *provider_aliases, *aliases)
        for key in keys:
            self._agents[key] = provider

    def register_realtime(self, provider: RealtimeProvider, *aliases: str) -> None:
        provider_aliases = getattr(provider, "provider_aliases", ())
        keys = (provider.provider_id, *provider_aliases, *aliases)
        for key in keys:
            self._realtime[key] = provider

    def get_model(self, provider: str) -> ModelProvider:
        ref = ProviderRef.parse(provider)
        try:
            return self._models[ref.provider_key]
        except KeyError as exc:
            known = ", ".join(sorted(self._models)) or "none"
            raise ProviderNotFoundError(
                f"No model provider registered for '{ref.provider_key}'. Known: {known}."
            ) from exc

    def get_agent(self, provider: str) -> AgentProvider:
        ref = ProviderRef.parse(provider)
        try:
            return self._agents[ref.provider_key]
        except KeyError as exc:
            known = ", ".join(sorted(self._agents)) or "none"
            raise ProviderNotFoundError(
                f"No agent provider registered for '{ref.provider_key}'. Known: {known}."
            ) from exc

    def get_realtime(self, provider: str) -> RealtimeProvider:
        ref = ProviderRef.parse(provider)
        try:
            return self._realtime[ref.provider_key]
        except KeyError as exc:
            known = ", ".join(sorted(self._realtime)) or "none"
            raise ProviderNotFoundError(
                f"No realtime provider registered for '{ref.provider_key}'. Known: {known}."
            ) from exc

    def list_model_providers(self) -> list[str]:
        return sorted(self._models)

    def list_agent_providers(self) -> list[str]:
        return sorted(self._agents)

    def list_realtime(self) -> dict[str, RealtimeProvider]:
        return dict(sorted(self._realtime.items()))

    def list_realtime_providers(self) -> list[str]:
        return sorted(self._realtime)

    async def close(self) -> None:
        """Close registered providers that expose an async/sync close hook."""
        seen: set[int] = set()
        for provider in [*self._models.values(), *self._agents.values(), *self._realtime.values()]:
            marker = id(provider)
            if marker in seen:
                continue
            seen.add(marker)
            close = getattr(provider, "close", None)
            if not callable(close):
                continue
            result = close()
            if hasattr(result, "__await__"):
                await result
