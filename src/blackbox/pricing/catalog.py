from __future__ import annotations

from collections.abc import Iterable

from blackbox.core.accounting import MarkupPolicy, ModelCatalog, ModelPricing
from blackbox.providers.catalog import bundled_provider_model_catalog
from blackbox.providers.model_catalog import ProviderModelCatalog

BUNDLED_PRICING_CATALOG_VERSION = "2026-05-06"
BUNDLED_PRICING_RETRIEVED_AT = "2026-05-06"
BUNDLED_PRICING_SOURCE = "blackbox-bundled"

_OPENAI_PRICING_URL = "https://openai.com/api/pricing/"
_ANTHROPIC_PRICING_URL = "https://docs.claude.com/en/docs/about-claude/pricing"
_GEMINI_PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"
_XAI_PRICING_URL = "https://docs.x.ai/docs/models"


def bundled_provider_pricing() -> list[ModelPricing]:
    """Return bundled provider API token pricing for common text models.

    The catalog intentionally tracks standard pay-as-you-go text token rates.
    It does not model batch discounts, long-context premiums, regional premiums,
    storage-hour charges, or per-tool call charges.
    """
    pricing: list[ModelPricing] = []
    pricing.extend(_openai_pricing())
    pricing.extend(_anthropic_pricing())
    pricing.extend(_gemini_pricing())
    pricing.extend(_xai_pricing())
    return pricing


def bundled_model_catalog(
    *,
    billing_policy: MarkupPolicy | None = None,
    extra_provider_pricing: Iterable[ModelPricing] = (),
    extra_billable_pricing: Iterable[ModelPricing] = (),
    provider_models: ProviderModelCatalog | None = None,
) -> ModelCatalog:
    """Build a model catalog seeded with bundled provider API costs."""
    catalog = ModelCatalog(billing_policy=billing_policy)
    catalog.register_many(bundled_provider_pricing())
    catalog.register_many(list(extra_provider_pricing))
    catalog.register_many(list(extra_billable_pricing), kind="billable")
    _register_provider_model_aliases(
        catalog,
        provider_models or bundled_provider_model_catalog(),
    )
    return catalog


def _register_provider_model_aliases(
    catalog: ModelCatalog,
    provider_models: ProviderModelCatalog,
) -> None:
    for model in provider_models.list():
        catalog.register_model_aliases(
            provider=model.provider,
            model=model.model,
            aliases=model.aliases,
        )


def _pricing(
    *,
    provider: str,
    model: str,
    input_per_million: float,
    output_per_million: float,
    source_url: str,
    cached_input_per_million: float | None = None,
    cache_read_input_per_million: float | None = None,
    cache_creation_input_per_million: float | None = None,
) -> ModelPricing:
    return ModelPricing(
        provider=provider,
        model=model,
        input_per_million=input_per_million,
        output_per_million=output_per_million,
        cached_input_per_million=cached_input_per_million,
        cache_read_input_per_million=cache_read_input_per_million,
        cache_creation_input_per_million=cache_creation_input_per_million,
        source=BUNDLED_PRICING_SOURCE,
        catalog_version=BUNDLED_PRICING_CATALOG_VERSION,
        retrieved_at=BUNDLED_PRICING_RETRIEVED_AT,
        source_url=source_url,
    )


def _openai_pricing() -> list[ModelPricing]:
    return [
        _pricing(
            provider="openai",
            model="gpt-5.5",
            input_per_million=5.00,
            cached_input_per_million=0.50,
            output_per_million=30.00,
            source_url=_OPENAI_PRICING_URL,
        ),
        _pricing(
            provider="openai",
            model="gpt-5.4",
            input_per_million=2.50,
            cached_input_per_million=0.25,
            output_per_million=15.00,
            source_url=_OPENAI_PRICING_URL,
        ),
        _pricing(
            provider="openai",
            model="gpt-5.4-mini",
            input_per_million=0.75,
            cached_input_per_million=0.075,
            output_per_million=4.50,
            source_url=_OPENAI_PRICING_URL,
        ),
    ]


def _anthropic_pricing() -> list[ModelPricing]:
    return [
        *_anthropic_aliases(
            models=("claude-opus-4-1", "claude-opus-4-1-20250805"),
            input_per_million=15.00,
            output_per_million=75.00,
        ),
        *_anthropic_aliases(
            models=("claude-opus-4", "claude-opus-4-20250514"),
            input_per_million=15.00,
            output_per_million=75.00,
        ),
        *_anthropic_aliases(
            models=("claude-sonnet-4-5", "claude-sonnet-4-5-20250929"),
            input_per_million=3.00,
            output_per_million=15.00,
        ),
        *_anthropic_aliases(
            models=("claude-sonnet-4", "claude-sonnet-4-20250514"),
            input_per_million=3.00,
            output_per_million=15.00,
        ),
        *_anthropic_aliases(
            models=("claude-haiku-4-5", "claude-haiku-4-5-20251001"),
            input_per_million=1.00,
            output_per_million=5.00,
        ),
        *_anthropic_aliases(
            models=("claude-haiku-3-5", "claude-3-5-haiku-20241022"),
            input_per_million=0.80,
            output_per_million=4.00,
        ),
    ]


def _anthropic_aliases(
    *,
    models: tuple[str, ...],
    input_per_million: float,
    output_per_million: float,
) -> list[ModelPricing]:
    return [
        _pricing(
            provider="anthropic",
            model=model,
            input_per_million=input_per_million,
            cache_creation_input_per_million=input_per_million * 1.25,
            cache_read_input_per_million=input_per_million * 0.1,
            output_per_million=output_per_million,
            source_url=_ANTHROPIC_PRICING_URL,
        )
        for model in models
    ]


def _gemini_pricing() -> list[ModelPricing]:
    return [
        _pricing(
            provider="google",
            model="gemini-3-flash-preview",
            input_per_million=0.50,
            cached_input_per_million=0.05,
            output_per_million=3.00,
            source_url=_GEMINI_PRICING_URL,
        ),
        _pricing(
            provider="google",
            model="gemini-2.5-pro",
            input_per_million=1.25,
            cached_input_per_million=0.125,
            output_per_million=10.00,
            source_url=_GEMINI_PRICING_URL,
        ),
        _pricing(
            provider="google",
            model="gemini-2.5-flash",
            input_per_million=0.30,
            cached_input_per_million=0.03,
            output_per_million=2.50,
            source_url=_GEMINI_PRICING_URL,
        ),
        _pricing(
            provider="google",
            model="gemini-2.5-flash-lite",
            input_per_million=0.10,
            cached_input_per_million=0.01,
            output_per_million=0.40,
            source_url=_GEMINI_PRICING_URL,
        ),
    ]


def _xai_pricing() -> list[ModelPricing]:
    return [
        _pricing(
            provider="xai",
            model="grok-4-1-fast-reasoning",
            input_per_million=0.20,
            output_per_million=0.50,
            source_url=_XAI_PRICING_URL,
        ),
        _pricing(
            provider="xai",
            model="grok-4-1-fast-non-reasoning",
            input_per_million=0.20,
            output_per_million=0.50,
            source_url=_XAI_PRICING_URL,
        ),
    ]
