from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.core.errors import ProviderNotFoundError
from agent_runtime.providers.base import AgentProvider, ModelProvider


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

    def register_model(self, provider: ModelProvider, *aliases: str) -> None:
        keys = (provider.provider_id, *aliases)
        for key in keys:
            self._models[key] = provider

    def register_agent(self, provider: AgentProvider, *aliases: str) -> None:
        keys = (provider.provider_id, *aliases)
        for key in keys:
            self._agents[key] = provider

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

    def list_model_providers(self) -> list[str]:
        return sorted(self._models)

    def list_agent_providers(self) -> list[str]:
        return sorted(self._agents)
