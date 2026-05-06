from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from blackbox.providers.model_catalog import ModelLifecycle, ProviderModel, ProviderModelCatalog

BUNDLED_PROVIDER_MODEL_CATALOG_VERSION = "2026-05-06"
BUNDLED_PROVIDER_MODEL_RETRIEVED_AT = "2026-05-06"
BUNDLED_PROVIDER_MODEL_SOURCE = "blackbox-bundled"

_OPENAI_MODELS_URL = "https://developers.openai.com/api/docs/models"
_ANTHROPIC_MODELS_URL = "https://platform.claude.com/docs/en/about-claude/models/overview"
_GEMINI_MODELS_URL = "https://ai.google.dev/gemini-api/docs/models"
_XAI_MODELS_URL = "https://docs.x.ai/developers/models"
_XAI_DEPRECATION_URL = "https://docs.x.ai/developers/migration/may-15-deprecation"


def bundled_provider_models() -> list[ProviderModel]:
    """Return bundled provider-native model metadata.

    This catalog tracks model identity, lifecycle, aliases, modalities, and
    capacity hints. It intentionally does not carry token prices.
    """
    models: list[ProviderModel] = []
    models.extend(_openai_models())
    models.extend(_anthropic_models())
    models.extend(_gemini_models())
    models.extend(_xai_models())
    return models


def bundled_provider_model_catalog(
    *,
    extra_models: Iterable[ProviderModel] = (),
) -> ProviderModelCatalog:
    catalog = ProviderModelCatalog()
    catalog.register_many(bundled_provider_models())
    catalog.register_many(extra_models)
    return catalog


def _model(
    *,
    provider: str,
    model: str,
    source_url: str,
    display_name: str | None = None,
    family: str | None = None,
    aliases: tuple[str, ...] = (),
    lifecycle: ModelLifecycle = "active",
    replacement_model: str | None = None,
    modalities: tuple[str, ...] = ("text",),
    context_window: int | None = None,
    max_output_tokens: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProviderModel:
    return ProviderModel(
        provider=provider,
        model=model,
        display_name=display_name,
        family=family,
        aliases=aliases,
        lifecycle=lifecycle,
        replacement_model=replacement_model,
        modalities=modalities,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        source=BUNDLED_PROVIDER_MODEL_SOURCE,
        catalog_version=BUNDLED_PROVIDER_MODEL_CATALOG_VERSION,
        retrieved_at=BUNDLED_PROVIDER_MODEL_RETRIEVED_AT,
        source_url=source_url,
        metadata=metadata or {},
    )


def _openai_models() -> list[ProviderModel]:
    metadata = {"reasoning_efforts": ["none", "low", "medium", "high", "xhigh"]}
    return [
        _model(
            provider="openai",
            model="gpt-5.5",
            display_name="GPT-5.5",
            family="gpt-5",
            modalities=("text", "image", "tools"),
            context_window=1_000_000,
            max_output_tokens=128_000,
            source_url=_OPENAI_MODELS_URL,
            metadata={**metadata, "knowledge_cutoff": "2025-12-01"},
        ),
        _model(
            provider="openai",
            model="gpt-5.4",
            display_name="GPT-5.4",
            family="gpt-5",
            modalities=("text", "image", "tools"),
            context_window=1_050_000,
            max_output_tokens=128_000,
            source_url=_OPENAI_MODELS_URL,
            metadata={**metadata, "knowledge_cutoff": "2025-08-31"},
        ),
        _model(
            provider="openai",
            model="gpt-5.4-mini",
            display_name="GPT-5.4 mini",
            family="gpt-5",
            aliases=("gpt-5.4-mini-2026-03-17",),
            modalities=("text", "image", "tools"),
            context_window=400_000,
            max_output_tokens=128_000,
            source_url=_OPENAI_MODELS_URL,
            metadata={**metadata, "knowledge_cutoff": "2025-08-31"},
        ),
    ]


def _anthropic_models() -> list[ProviderModel]:
    current_modalities = ("text", "image", "tools")
    return [
        _model(
            provider="anthropic",
            model="claude-opus-4-7",
            display_name="Claude Opus 4.7",
            family="claude-opus",
            modalities=current_modalities,
            context_window=1_000_000,
            max_output_tokens=128_000,
            source_url=_ANTHROPIC_MODELS_URL,
            metadata={"training_cutoff": "2026-01"},
        ),
        _model(
            provider="anthropic",
            model="claude-sonnet-4-6",
            display_name="Claude Sonnet 4.6",
            family="claude-sonnet",
            modalities=current_modalities,
            context_window=1_000_000,
            max_output_tokens=64_000,
            source_url=_ANTHROPIC_MODELS_URL,
            metadata={"training_cutoff": "2026-01"},
        ),
        _model(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            display_name="Claude Haiku 4.5",
            family="claude-haiku",
            aliases=("claude-haiku-4-5",),
            modalities=current_modalities,
            context_window=200_000,
            max_output_tokens=64_000,
            source_url=_ANTHROPIC_MODELS_URL,
            metadata={"training_cutoff": "2025-07"},
        ),
        *_anthropic_legacy_models(),
    ]


def _anthropic_legacy_models() -> list[ProviderModel]:
    return [
        _model(
            provider="anthropic",
            model="claude-opus-4-1",
            display_name="Claude Opus 4.1",
            family="claude-opus",
            aliases=("claude-opus-4-1-20250805",),
            lifecycle="unknown",
            replacement_model="claude-opus-4-7",
            source_url=_ANTHROPIC_MODELS_URL,
        ),
        _model(
            provider="anthropic",
            model="claude-opus-4",
            display_name="Claude Opus 4",
            family="claude-opus",
            aliases=("claude-opus-4-20250514",),
            lifecycle="unknown",
            replacement_model="claude-opus-4-7",
            source_url=_ANTHROPIC_MODELS_URL,
        ),
        _model(
            provider="anthropic",
            model="claude-sonnet-4-5",
            display_name="Claude Sonnet 4.5",
            family="claude-sonnet",
            aliases=("claude-sonnet-4-5-20250929",),
            lifecycle="unknown",
            replacement_model="claude-sonnet-4-6",
            context_window=200_000,
            max_output_tokens=64_000,
            source_url=_ANTHROPIC_MODELS_URL,
            metadata={"context_window_beta": 1_000_000},
        ),
        _model(
            provider="anthropic",
            model="claude-sonnet-4",
            display_name="Claude Sonnet 4",
            family="claude-sonnet",
            aliases=("claude-sonnet-4-20250514",),
            lifecycle="unknown",
            replacement_model="claude-sonnet-4-6",
            source_url=_ANTHROPIC_MODELS_URL,
        ),
        _model(
            provider="anthropic",
            model="claude-haiku-3-5",
            display_name="Claude Haiku 3.5",
            family="claude-haiku",
            aliases=("claude-3-5-haiku-20241022",),
            lifecycle="unknown",
            replacement_model="claude-haiku-4-5",
            source_url=_ANTHROPIC_MODELS_URL,
        ),
    ]


def _gemini_models() -> list[ProviderModel]:
    gemini_text_modalities = ("text", "image", "audio", "video", "pdf", "tools")
    return [
        _model(
            provider="google",
            model="gemini-3-flash-preview",
            display_name="Gemini 3 Flash Preview",
            family="gemini-3",
            lifecycle="preview",
            modalities=gemini_text_modalities,
            source_url=_GEMINI_MODELS_URL,
        ),
        _model(
            provider="google",
            model="gemini-2.5-pro",
            display_name="Gemini 2.5 Pro",
            family="gemini-2.5",
            modalities=gemini_text_modalities,
            context_window=1_048_576,
            max_output_tokens=65_536,
            source_url=_GEMINI_MODELS_URL,
        ),
        _model(
            provider="google",
            model="gemini-2.5-flash",
            display_name="Gemini 2.5 Flash",
            family="gemini-2.5",
            modalities=gemini_text_modalities,
            context_window=1_048_576,
            max_output_tokens=65_536,
            source_url=_GEMINI_MODELS_URL,
        ),
        _model(
            provider="google",
            model="gemini-2.5-flash-lite",
            display_name="Gemini 2.5 Flash-Lite",
            family="gemini-2.5",
            modalities=gemini_text_modalities,
            context_window=1_048_576,
            max_output_tokens=65_536,
            source_url=_GEMINI_MODELS_URL,
        ),
    ]


def _xai_models() -> list[ProviderModel]:
    deprecation_metadata = {
        "deprecates_at": "2026-05-15T12:00:00-07:00",
        "deprecation_url": _XAI_DEPRECATION_URL,
    }
    return [
        _model(
            provider="xai",
            model="grok-4.3",
            display_name="Grok 4.3",
            family="grok-4",
            modalities=("text", "image", "tools"),
            context_window=1_000_000,
            source_url=_XAI_MODELS_URL,
        ),
        _model(
            provider="xai",
            model="grok-4.20-0309-non-reasoning",
            display_name="Grok 4.20 non-reasoning",
            family="grok-4",
            aliases=("grok-4.20-non-reasoning",),
            modalities=("text", "image", "tools"),
            context_window=2_000_000,
            source_url=_XAI_MODELS_URL,
        ),
        _model(
            provider="xai",
            model="grok-4-1-fast-reasoning",
            display_name="Grok 4.1 Fast Reasoning",
            family="grok-4",
            lifecycle="deprecating",
            replacement_model="grok-4.3",
            modalities=("text", "image", "tools"),
            context_window=2_000_000,
            source_url=_XAI_MODELS_URL,
            metadata=deprecation_metadata,
        ),
        _model(
            provider="xai",
            model="grok-4-1-fast-non-reasoning",
            display_name="Grok 4.1 Fast Non-Reasoning",
            family="grok-4",
            lifecycle="deprecating",
            replacement_model="grok-4.20-0309-non-reasoning",
            modalities=("text", "image", "tools"),
            context_window=2_000_000,
            source_url=_XAI_MODELS_URL,
            metadata=deprecation_metadata,
        ),
    ]
