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
    assert xai.lifecycle == "retired"
    assert xai.replacement_model == "grok-4.3"
    assert xai.metadata["deprecates_at"] == "2026-05-15T12:00:00-07:00"
    assert {openai.source, anthropic.source, gemini.source, xai.source} == {"blackbox-bundled"}
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
    assert [model.model for model in catalog.list(provider="xai", lifecycle="retired")] == [
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


def test_current_catalog_identity_capacity_and_retirement_provenance() -> None:
    catalog = bundled_provider_model_catalog()
    new_ids = {
        "openai": ("gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"),
        "anthropic": (
            "claude-fable-5-1",
            "claude-fable-5",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-opus-4-8",
        ),
        "xai": ("grok-4.6",),
    }
    for provider, models in new_ids.items():
        for model_id in models:
            model = catalog.get(provider=provider, model=model_id)
            assert model is not None
            assert model.lifecycle == "active"
            assert model.retrieved_at == "2026-09-05"
            assert (
                model.context_window
                == {"openai": 1_050_000, "anthropic": 1_000_000, "xai": 500_000}[provider]
            )
            assert model.max_output_tokens == (None if provider == "xai" else 128_000)
            assert model.source_url
    sol = catalog.get(provider="openai", model="gpt-5.6")
    assert sol is not None and sol.model == "gpt-5.6-sol"
    old = catalog.get(provider="google", model="gemini-2.5-flash")
    assert old is not None and old.retrieved_at == "2026-05-06"
    for model_id, replacement in (
        ("claude-opus-4-1", "claude-opus-4-8"),
        ("claude-opus-4", "claude-opus-4-8"),
        ("claude-sonnet-4", "claude-sonnet-4-6"),
        ("claude-haiku-3-5", "claude-haiku-4-5"),
    ):
        model = catalog.get(provider="anthropic", model=model_id)
        assert model is not None and model.lifecycle == "retired"
        assert model.replacement_model == replacement
        assert model.model == model_id
    sonnet = catalog.get(provider="anthropic", model="claude-sonnet-4-5")
    assert sonnet is not None and sonnet.lifecycle == "active"
