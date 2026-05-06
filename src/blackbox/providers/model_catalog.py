from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

ModelLifecycle = Literal["active", "preview", "deprecated", "retired", "unknown"]


@dataclass(slots=True, frozen=True)
class ProviderModel:
    """Provider-native model metadata independent of pricing.

    Pricing catalogs can attach monetary information to the same
    ``provider``/``model`` identity, but this record owns model identity,
    aliases, lifecycle, modalities, and capacity hints.
    """

    provider: str
    model: str
    display_name: str | None = None
    family: str | None = None
    aliases: tuple[str, ...] = ()
    lifecycle: ModelLifecycle = "unknown"
    replacement_model: str | None = None
    modalities: tuple[str, ...] = ("text",)
    context_window: int | None = None
    max_output_tokens: int | None = None
    source: str | None = None
    catalog_version: str | None = None
    retrieved_at: str | None = None
    source_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(slots=True)
class ProviderModelCatalog:
    """In-memory catalog of provider-native model identities and metadata."""

    _models: dict[tuple[str, str], ProviderModel] = field(default_factory=dict)
    _aliases: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)

    def register(self, model: ProviderModel) -> None:
        key = (model.provider, model.model)
        self._models[key] = model
        self._aliases[key] = key
        for alias in model.aliases:
            self._aliases[(model.provider, alias)] = key

    def register_many(self, models: Iterable[ProviderModel]) -> None:
        for model in models:
            self.register(model)

    def get(
        self,
        *,
        provider: str,
        model: str,
        resolve_aliases: bool = True,
    ) -> ProviderModel | None:
        key = (provider, model)
        if resolve_aliases:
            key = self._aliases.get(key, key)
        return self._models.get(key)

    def resolve(self, *, provider: str, model: str) -> tuple[str, str] | None:
        key = self._aliases.get((provider, model))
        if key is None and (provider, model) in self._models:
            return (provider, model)
        return key

    def contains(self, *, provider: str, model: str) -> bool:
        return self.resolve(provider=provider, model=model) is not None

    def list(
        self,
        *,
        provider: str | None = None,
        lifecycle: ModelLifecycle | None = None,
        modality: str | None = None,
        family: str | None = None,
    ) -> list[ProviderModel]:
        models: Iterable[ProviderModel] = self._models.values()
        if provider is not None:
            models = (model for model in models if model.provider == provider)
        if lifecycle is not None:
            models = (model for model in models if model.lifecycle == lifecycle)
        if modality is not None:
            models = (model for model in models if modality in model.modalities)
        if family is not None:
            models = (model for model in models if model.family == family)
        return sorted(models, key=lambda item: (item.provider, item.model))

    def aliases_for(self, *, provider: str, model: str) -> tuple[str, ...]:
        canonical = self.get(provider=provider, model=model)
        if canonical is None:
            return ()
        return canonical.aliases
