from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Any

from blackbox.core.cache import ProviderCacheRecord, ProviderCacheStore
from blackbox.core.errors import ConfigurationError, ProviderExecutionError
from blackbox.providers.registry import ProviderRef, ProviderRegistry


@dataclass(slots=True)
class ProviderCacheRuntime:
    """Facade for provider cache lifecycle and local cache registry operations."""

    registry: ProviderRegistry
    store: ProviderCacheStore

    async def create(
        self,
        *,
        provider: str,
        contents: Any,
        model: str | None = None,
        key: str | None = None,
        system_instruction: str | None = None,
        ttl: str | None = None,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ProviderCacheRecord:
        """Create a provider-side cache record and persist it locally."""

        provider_ref = ProviderRef.parse(provider)
        model_name = model or provider_ref.resource
        if not model_name:
            raise ValueError("model must be provided explicitly or as 'provider/model'.")
        adapter = self.registry.get_model(provider_ref.provider_key)
        create_cache = getattr(adapter, "create_cache", None)
        if not callable(create_cache):
            raise ConfigurationError(
                f"Provider '{provider_ref.provider_key}' does not expose provider-side cache creation."
            )
        result = create_cache(
            model=model_name,
            contents=contents,
            key=key,
            system_instruction=system_instruction,
            ttl=ttl,
            display_name=display_name,
            metadata=metadata,
            **kwargs,
        )
        record = await result if hasattr(result, "__await__") else result
        if not isinstance(record, ProviderCacheRecord):
            raise ProviderExecutionError(
                f"Provider '{provider_ref.provider_key}' returned an invalid cache record."
            )
        if key is not None and record.key is None:
            record.key = key
        if metadata:
            record.metadata.update(metadata)
        await self.store.save(record)
        return record

    async def get(
        self,
        *,
        provider: str,
        key: str | None = None,
        model: str | None = None,
        provider_cache_id: str | None = None,
    ) -> ProviderCacheRecord | None:
        provider_ref = ProviderRef.parse(provider)
        if provider_cache_id is not None:
            return await self.store.load_by_provider_cache_id(
                provider=provider_ref.provider_key,
                provider_cache_id=provider_cache_id,
            )
        if key is None:
            raise ValueError("Pass key or provider_cache_id.")
        return await self.store.load(
            provider=provider_ref.provider_key,
            model=model or provider_ref.resource,
            key=key,
        )

    async def list(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        include_expired: bool = True,
    ) -> builtins.list[ProviderCacheRecord]:
        provider_key = ProviderRef.parse(provider).provider_key if provider else None
        return await self.store.list_records(
            provider=provider_key,
            model=model,
            include_expired=include_expired,
        )

    async def invalidate(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        key: str | None = None,
        provider_cache_id: str | None = None,
        record_id: str | None = None,
        delete_provider_cache: bool = False,
    ) -> builtins.list[ProviderCacheRecord]:
        """Delete local cache records and optionally remove matching provider caches."""

        provider_key = ProviderRef.parse(provider).provider_key if provider else None
        deleted = await self.store.delete(
            provider=provider_key,
            model=model,
            key=key,
            provider_cache_id=provider_cache_id,
            record_id=record_id,
        )
        if delete_provider_cache:
            await self._delete_provider_caches(deleted)
        return deleted

    async def evict_expired(
        self, *, delete_provider_cache: bool = False
    ) -> builtins.list[ProviderCacheRecord]:
        evicted = await self.store.evict_expired()
        if delete_provider_cache:
            await self._delete_provider_caches(evicted)
        return evicted

    async def _delete_provider_caches(
        self, records: builtins.list[ProviderCacheRecord]
    ) -> None:
        for record in records:
            if record.provider_cache_id is None:
                continue
            adapter = self.registry.get_model(record.provider)
            delete_cache = getattr(adapter, "delete_cache", None)
            if not callable(delete_cache):
                continue
            result = delete_cache(record.provider_cache_id)
            if hasattr(result, "__await__"):
                await result
