from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_runtime import AgentRuntime, ProviderCacheRecord, SQLiteProviderCacheStore
from agent_runtime.providers.model_adapters.gemini_generate_content import (
    GeminiGenerateContentProvider,
)


class FakeGeminiCaches:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.created.append(kwargs)
        return SimpleNamespace(
            name="cachedContents/abc123",
            expire_time="2026-04-27T12:00:00Z",
            usage_metadata=SimpleNamespace(total_token_count=2048),
        )

    def delete(self, *, name: str) -> None:
        self.deleted.append(name)


class FakeGeminiCacheClient:
    def __init__(self) -> None:
        self.caches = FakeGeminiCaches()


async def test_runtime_creates_persists_and_invalidates_provider_cache(
    tmp_path: Path,
) -> None:
    store = SQLiteProviderCacheStore(tmp_path / "provider-caches.sqlite")
    client = FakeGeminiCacheClient()
    runtime = AgentRuntime(provider_cache_store=store)
    runtime.registry.register_model(GeminiGenerateContentProvider(client=client))

    record = await runtime.caches.create(
        provider="google:gemini-test",
        key="docs-v1",
        contents=[{"role": "user", "parts": [{"text": "stable docs"}]}],
        system_instruction="Reuse this context.",
        ttl="300s",
        display_name="Docs v1",
    )
    loaded = await runtime.caches.get(
        provider="google", model="gemini-test", key="docs-v1"
    )
    deleted = await runtime.caches.invalidate(
        provider="google",
        model="gemini-test",
        key="docs-v1",
        delete_provider_cache=True,
    )

    assert record.provider_cache_id == "cachedContents/abc123"
    assert loaded is not None
    assert loaded.provider_cache_id == "cachedContents/abc123"
    assert client.caches.created[0]["config"]["ttl"] == "300s"
    assert client.caches.created[0]["config"]["system_instruction"] == "Reuse this context."
    assert [item.provider_cache_id for item in deleted] == ["cachedContents/abc123"]
    assert client.caches.deleted == ["cachedContents/abc123"]
    await store.close()


async def test_sqlite_provider_cache_store_evicts_expired_records(tmp_path: Path) -> None:
    store = SQLiteProviderCacheStore(tmp_path / "provider-caches.sqlite")
    now = datetime.now(UTC)
    expired = ProviderCacheRecord(
        provider="google",
        model="gemini-test",
        key="old",
        provider_cache_id="cachedContents/old",
        expires_at=now - timedelta(seconds=1),
    )
    fresh = ProviderCacheRecord(
        provider="google",
        model="gemini-test",
        key="fresh",
        provider_cache_id="cachedContents/fresh",
        expires_at=now + timedelta(seconds=60),
    )
    await store.save(expired)
    await store.save(fresh)

    evicted = await store.evict_expired(now=now)
    remaining = await store.list_records()

    assert [record.key for record in evicted] == ["old"]
    assert [record.key for record in remaining] == ["fresh"]
    await store.close()
