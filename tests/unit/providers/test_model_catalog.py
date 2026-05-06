from __future__ import annotations

from blackbox.providers.model_catalog import ProviderModel, ProviderModelCatalog


def test_provider_model_catalog_resolves_aliases() -> None:
    catalog = ProviderModelCatalog()
    catalog.register(
        ProviderModel(
            provider="openai",
            model="gpt-5.4-mini",
            aliases=("gpt-5-mini",),
            lifecycle="active",
        )
    )

    model = catalog.get(provider="openai", model="gpt-5-mini")

    assert model is not None
    assert model.model == "gpt-5.4-mini"
    assert model.ref == "openai:gpt-5.4-mini"
    assert catalog.resolve(provider="openai", model="gpt-5-mini") == (
        "openai",
        "gpt-5.4-mini",
    )


def test_provider_model_catalog_filters_models() -> None:
    catalog = ProviderModelCatalog()
    catalog.register_many(
        [
            ProviderModel(
                provider="openai",
                model="gpt-5.4-mini",
                family="gpt-5",
                lifecycle="active",
                modalities=("text", "tools"),
            ),
            ProviderModel(
                provider="openai",
                model="gpt-image-1",
                family="gpt-image",
                lifecycle="active",
                modalities=("image",),
            ),
            ProviderModel(
                provider="anthropic",
                model="claude-3-5-haiku-20241022",
                family="claude-haiku",
                lifecycle="deprecated",
                replacement_model="claude-haiku-4-5",
            ),
        ]
    )

    assert [model.model for model in catalog.list(provider="openai")] == [
        "gpt-5.4-mini",
        "gpt-image-1",
    ]
    assert [model.model for model in catalog.list(modality="image")] == ["gpt-image-1"]
    assert [model.model for model in catalog.list(lifecycle="deprecated")] == [
        "claude-3-5-haiku-20241022"
    ]


def test_provider_model_catalog_reports_contains_and_aliases() -> None:
    catalog = ProviderModelCatalog()
    catalog.register(
        ProviderModel(
            provider="anthropic",
            model="claude-sonnet-4-5-20250929",
            aliases=("claude-sonnet-4-5",),
        )
    )

    assert catalog.contains(provider="anthropic", model="claude-sonnet-4-5")
    assert catalog.aliases_for(
        provider="anthropic",
        model="claude-sonnet-4-5-20250929",
    ) == ("claude-sonnet-4-5",)
    assert catalog.aliases_for(provider="missing", model="model") == ()
