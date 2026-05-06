from __future__ import annotations

import blackbox
from blackbox.providers.catalog import (
    BUNDLED_PROVIDER_MODEL_CATALOG_VERSION,
    bundled_provider_model_catalog,
    bundled_provider_models,
)
from blackbox.providers.model_catalog import ProviderModel


def test_bundled_provider_models_include_common_provider_metadata() -> None:
    models = bundled_provider_models()
    by_key = {(model.provider, model.model): model for model in models}

    openai = by_key[("openai", "gpt-5.4-mini")]
    anthropic = by_key[("anthropic", "claude-haiku-4-5-20251001")]
    gemini = by_key[("google", "gemini-2.5-flash")]
    xai = by_key[("xai", "grok-4-1-fast-reasoning")]

    assert openai.context_window == 400_000
    assert openai.max_output_tokens == 128_000
    assert "tools" in openai.modalities
    assert anthropic.aliases == ("claude-haiku-4-5",)
    assert anthropic.max_output_tokens == 64_000
    assert gemini.context_window == 1_048_576
    assert "pdf" in gemini.modalities
    assert xai.lifecycle == "deprecating"
    assert xai.replacement_model == "grok-4.3"
    assert xai.metadata["deprecates_at"] == "2026-05-15T12:00:00-07:00"
    assert {openai.source, anthropic.source, gemini.source, xai.source} == {
        "blackbox-bundled"
    }
    assert {
        openai.catalog_version,
        anthropic.catalog_version,
        gemini.catalog_version,
        xai.catalog_version,
    } == {BUNDLED_PROVIDER_MODEL_CATALOG_VERSION}


def test_bundled_provider_model_catalog_resolves_aliases_and_filters() -> None:
    catalog = bundled_provider_model_catalog()

    claude = catalog.get(provider="anthropic", model="claude-haiku-4-5")
    xai = catalog.get(provider="xai", model="grok-4.20-non-reasoning")

    assert claude is not None
    assert claude.model == "claude-haiku-4-5-20251001"
    assert xai is not None
    assert xai.model == "grok-4.20-0309-non-reasoning"
    assert [model.model for model in catalog.list(provider="xai", lifecycle="deprecating")] == [
        "grok-4-1-fast-non-reasoning",
        "grok-4-1-fast-reasoning",
    ]


def test_bundled_provider_model_catalog_accepts_user_models() -> None:
    catalog = bundled_provider_model_catalog(
        extra_models=[
            ProviderModel(
                provider="tenant",
                model="custom-prod",
                aliases=("custom",),
                lifecycle="active",
                context_window=32_000,
                source="tenant",
            )
        ]
    )

    model = catalog.get(provider="tenant", model="custom")

    assert model is not None
    assert model.model == "custom-prod"
    assert model.source == "tenant"


def test_provider_model_catalog_is_exported_from_public_entrypoint() -> None:
    catalog = blackbox.bundled_provider_model_catalog()

    assert isinstance(catalog, blackbox.ProviderModelCatalog)
