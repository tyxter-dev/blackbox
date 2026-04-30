from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from blackbox.core.accounting import ModelUsage, usage_from_mapping


@dataclass(slots=True)
class ProviderCacheRecord:
    """Registry record for provider-managed prompt/context caches."""

    provider: str
    model: str
    key: str | None = None
    provider_cache_id: str | None = None
    strategy: str = "provider_managed"
    ttl: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    hits: int = 0
    misses: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"cache_{uuid4().hex}")

    @property
    def hit_rate(self) -> float | None:
        total = self.hits + self.misses
        if total == 0:
            return None
        return self.hits / total

    def to_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "key": self.key,
            "provider_cache_id": self.provider_cache_id,
            "strategy": self.strategy,
            "ttl": self.ttl,
            "hits": self.hits,
            "misses": self.misses,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }
        if self.hit_rate is not None:
            summary["hit_rate"] = self.hit_rate
        if self.expires_at is not None:
            summary["expires_at"] = self.expires_at.isoformat()
        if self.last_used_at is not None:
            summary["last_used_at"] = self.last_used_at.isoformat()
        if self.metadata:
            summary["metadata"] = dict(self.metadata)
        return summary


@dataclass(slots=True, frozen=True)
class ProviderCacheUsage:
    """Per-result prompt/context cache metrics."""

    provider: str
    model: str
    requested: bool = False
    key: str | None = None
    provider_cache_id: str | None = None
    strategy: str | None = None
    ttl: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def hit(self) -> bool:
        if self.cache_read_input_tokens > 0:
            return True
        return self.cached_input_tokens > 0 and self.cache_creation_input_tokens == 0

    @property
    def hit_ratio(self) -> float | None:
        if self.input_tokens <= 0:
            return None
        return self.cached_input_tokens / self.input_tokens

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "requested": self.requested,
            "hit": self.hit,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }
        if self.key is not None:
            payload["key"] = self.key
        if self.provider_cache_id is not None:
            payload["provider_cache_id"] = self.provider_cache_id
        if self.strategy is not None:
            payload["strategy"] = self.strategy
        if self.ttl is not None:
            payload["ttl"] = self.ttl
        if self.hit_ratio is not None:
            payload["hit_ratio"] = self.hit_ratio
        return payload


@runtime_checkable
class ProviderCacheStore(Protocol):
    """Storage contract for provider-managed prompt/context cache records."""

    async def save(self, record: ProviderCacheRecord) -> None: ...

    async def load(
        self, *, provider: str, key: str, model: str | None = None
    ) -> ProviderCacheRecord | None:
        """Return the latest cache record matching provider, key, and optional model."""
        ...

    async def load_by_provider_cache_id(
        self, *, provider: str, provider_cache_id: str
    ) -> ProviderCacheRecord | None:
        """Return the latest cache record matching a provider-issued cache id."""
        ...

    async def list_records(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        include_expired: bool = True,
    ) -> list[ProviderCacheRecord]:
        """List cache records, optionally filtering by provider, model, and expiry."""
        ...

    async def delete(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        key: str | None = None,
        provider_cache_id: str | None = None,
        record_id: str | None = None,
    ) -> list[ProviderCacheRecord]:
        """Delete and return records matching at least one supplied selector."""
        ...

    async def evict_expired(
        self, *, now: datetime | None = None
    ) -> list[ProviderCacheRecord]: ...


@dataclass
class InMemoryProviderCacheStore:
    """Process-local provider cache registry for tests and simple deployments."""

    _records: dict[str, ProviderCacheRecord] = field(default_factory=dict)

    async def save(self, record: ProviderCacheRecord) -> None:
        self._records[record.id] = record

    async def load(
        self, *, provider: str, key: str, model: str | None = None
    ) -> ProviderCacheRecord | None:
        matches = [
            record
            for record in self._records.values()
            if record.provider == provider
            and record.key == key
            and (model is None or record.model == model)
        ]
        return _latest(matches)

    async def load_by_provider_cache_id(
        self, *, provider: str, provider_cache_id: str
    ) -> ProviderCacheRecord | None:
        matches = [
            record
            for record in self._records.values()
            if record.provider == provider
            and record.provider_cache_id == provider_cache_id
        ]
        return _latest(matches)

    async def list_records(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        include_expired: bool = True,
    ) -> list[ProviderCacheRecord]:
        now = datetime.now(UTC)
        return [
            record
            for record in self._records.values()
            if (provider is None or record.provider == provider)
            and (model is None or record.model == model)
            and (include_expired or not _expired(record, now))
        ]

    async def delete(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        key: str | None = None,
        provider_cache_id: str | None = None,
        record_id: str | None = None,
    ) -> list[ProviderCacheRecord]:
        deleted: list[ProviderCacheRecord] = []
        for record in list(self._records.values()):
            if not _matches_record(
                record,
                provider=provider,
                model=model,
                key=key,
                provider_cache_id=provider_cache_id,
                record_id=record_id,
            ):
                continue
            deleted.append(record)
            self._records.pop(record.id, None)
        return deleted

    async def evict_expired(
        self, *, now: datetime | None = None
    ) -> list[ProviderCacheRecord]:
        effective_now = now or datetime.now(UTC)
        expired = [
            record for record in self._records.values() if _expired(record, effective_now)
        ]
        for record in expired:
            self._records.pop(record.id, None)
        return expired


class SQLiteProviderCacheStore:
    """SQLite-backed provider cache registry."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._connection = sqlite3.connect(
            str(self._path), check_same_thread=False
        )
        self._connection.executescript(_SQLITE_SCHEMA)
        self._connection.commit()

    @property
    def path(self) -> Path:
        return self._path

    async def save(self, record: ProviderCacheRecord) -> None:
        payload = json.dumps(_record_to_dict(record))
        async with self._lock:
            await asyncio.to_thread(self._save_sync, record, payload)

    async def load(
        self, *, provider: str, key: str, model: str | None = None
    ) -> ProviderCacheRecord | None:
        async with self._lock:
            payload = await asyncio.to_thread(
                self._load_sync,
                "provider = ? AND key = ?"
                + (" AND model = ?" if model is not None else ""),
                (provider, key, model) if model is not None else (provider, key),
            )
        return _record_from_json(payload)

    async def load_by_provider_cache_id(
        self, *, provider: str, provider_cache_id: str
    ) -> ProviderCacheRecord | None:
        async with self._lock:
            payload = await asyncio.to_thread(
                self._load_sync,
                "provider = ? AND provider_cache_id = ?",
                (provider, provider_cache_id),
            )
        return _record_from_json(payload)

    async def list_records(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        include_expired: bool = True,
    ) -> list[ProviderCacheRecord]:
        async with self._lock:
            payloads = await asyncio.to_thread(self._list_sync)
        now = datetime.now(UTC)
        records = [
            record
            for payload in payloads
            if (record := _record_from_json(payload)) is not None
        ]
        return [
            record
            for record in records
            if (provider is None or record.provider == provider)
            and (model is None or record.model == model)
            and (include_expired or not _expired(record, now))
        ]

    async def delete(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        key: str | None = None,
        provider_cache_id: str | None = None,
        record_id: str | None = None,
    ) -> list[ProviderCacheRecord]:
        records = await self.list_records()
        deleted = [
            record
            for record in records
            if _matches_record(
                record,
                provider=provider,
                model=model,
                key=key,
                provider_cache_id=provider_cache_id,
                record_id=record_id,
            )
        ]
        if not deleted:
            return []
        async with self._lock:
            await asyncio.to_thread(self._delete_ids_sync, [record.id for record in deleted])
        return deleted

    async def evict_expired(
        self, *, now: datetime | None = None
    ) -> list[ProviderCacheRecord]:
        effective_now = now or datetime.now(UTC)
        records = await self.list_records()
        expired = [record for record in records if _expired(record, effective_now)]
        if not expired:
            return []
        async with self._lock:
            await asyncio.to_thread(self._delete_ids_sync, [record.id for record in expired])
        return expired

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._connection.close)

    def _save_sync(self, record: ProviderCacheRecord, payload: str) -> None:
        updated_at = datetime.now(UTC).isoformat()
        self._connection.execute(
            "INSERT OR REPLACE INTO provider_caches("
            "id, provider, model, key, provider_cache_id, expires_at, payload, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                record.provider,
                record.model,
                record.key,
                record.provider_cache_id,
                record.expires_at.isoformat() if record.expires_at else None,
                payload,
                updated_at,
            ),
        )
        self._connection.commit()

    def _load_sync(self, where: str, params: tuple[Any, ...]) -> str | None:
        cursor = self._connection.execute(
            f"SELECT payload FROM provider_caches WHERE {where} ORDER BY updated_at DESC LIMIT 1",
            params,
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _list_sync(self) -> list[str]:
        cursor = self._connection.execute(
            "SELECT payload FROM provider_caches ORDER BY updated_at DESC"
        )
        return [row[0] for row in cursor.fetchall()]

    def _delete_ids_sync(self, ids: list[str]) -> None:
        self._connection.executemany(
            "DELETE FROM provider_caches WHERE id = ?",
            [(id_,) for id_ in ids],
        )
        self._connection.commit()


async def record_provider_cache_usage(
    store: ProviderCacheStore,
    *,
    provider: str,
    model: str,
    key: str | None = None,
    provider_cache_id: str | None = None,
    strategy: str | None = None,
    ttl: str | None = None,
    usage: ModelUsage | dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProviderCacheRecord | None:
    """Update a cache record with usage-derived hit/miss and token counters.

    The record is located by key first and then provider cache id, creating one
    when either identifier is supplied.
    """

    if key is None and provider_cache_id is None:
        return None
    normalized = usage if isinstance(usage, ModelUsage) else usage_from_mapping(usage)
    existing: ProviderCacheRecord | None = None
    if key is not None:
        existing = await store.load(provider=provider, model=model, key=key)
    if existing is None and provider_cache_id is not None:
        existing = await store.load_by_provider_cache_id(
            provider=provider,
            provider_cache_id=provider_cache_id,
        )
    hit = _usage_cache_hit(normalized)
    base = existing or ProviderCacheRecord(
        provider=provider,
        model=model,
        key=key,
        provider_cache_id=provider_cache_id,
        strategy=strategy or "provider_managed",
        ttl=ttl,
    )
    updated = replace(
        base,
        key=base.key or key,
        provider_cache_id=base.provider_cache_id or provider_cache_id,
        strategy=strategy or base.strategy,
        ttl=ttl or base.ttl,
        last_used_at=datetime.now(UTC),
        hits=base.hits + (1 if hit else 0),
        misses=base.misses + (0 if hit else 1),
        input_tokens=base.input_tokens + normalized.input_tokens,
        cached_input_tokens=base.cached_input_tokens + normalized.cached_input_tokens,
        cache_read_input_tokens=base.cache_read_input_tokens
        + normalized.cache_read_input_tokens,
        cache_creation_input_tokens=base.cache_creation_input_tokens
        + normalized.cache_creation_input_tokens,
        metadata={**base.metadata, **(metadata or {})},
    )
    await store.save(updated)
    return updated


def cache_usage_from_usage(
    *,
    provider: str,
    model: str,
    usage: ModelUsage | dict[str, Any] | None,
    requested: bool = False,
    key: str | None = None,
    provider_cache_id: str | None = None,
    strategy: str | None = None,
    ttl: str | None = None,
) -> ProviderCacheUsage | None:
    """Build per-result cache usage metrics when caching was requested or observed."""

    normalized = usage if isinstance(usage, ModelUsage) else usage_from_mapping(usage)
    if (
        not requested
        and normalized.cached_input_tokens == 0
        and normalized.cache_read_input_tokens == 0
        and normalized.cache_creation_input_tokens == 0
    ):
        return None
    return ProviderCacheUsage(
        provider=provider,
        model=model,
        requested=requested,
        key=key,
        provider_cache_id=provider_cache_id,
        strategy=strategy,
        ttl=ttl,
        input_tokens=normalized.input_tokens,
        cached_input_tokens=normalized.cached_input_tokens,
        cache_read_input_tokens=normalized.cache_read_input_tokens,
        cache_creation_input_tokens=normalized.cache_creation_input_tokens,
    )


def _usage_cache_hit(usage: ModelUsage) -> bool:
    if usage.cache_read_input_tokens > 0:
        return True
    return usage.cached_input_tokens > 0 and usage.cache_creation_input_tokens == 0


def _latest(records: list[ProviderCacheRecord]) -> ProviderCacheRecord | None:
    if not records:
        return None
    return sorted(
        records,
        key=lambda record: record.last_used_at or record.created_at,
        reverse=True,
    )[0]


def _expired(record: ProviderCacheRecord, now: datetime) -> bool:
    return record.expires_at is not None and record.expires_at <= now


def _matches_record(
    record: ProviderCacheRecord,
    *,
    provider: str | None,
    model: str | None,
    key: str | None,
    provider_cache_id: str | None,
    record_id: str | None,
) -> bool:
    if provider is not None and record.provider != provider:
        return False
    if model is not None and record.model != model:
        return False
    if key is not None and record.key != key:
        return False
    if provider_cache_id is not None and record.provider_cache_id != provider_cache_id:
        return False
    if record_id is not None and record.id != record_id:
        return False
    return any(
        value is not None for value in (provider, model, key, provider_cache_id, record_id)
    )


def _record_to_dict(record: ProviderCacheRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "provider": record.provider,
        "model": record.model,
        "key": record.key,
        "provider_cache_id": record.provider_cache_id,
        "strategy": record.strategy,
        "ttl": record.ttl,
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "last_used_at": record.last_used_at.isoformat() if record.last_used_at else None,
        "hits": record.hits,
        "misses": record.misses,
        "input_tokens": record.input_tokens,
        "cached_input_tokens": record.cached_input_tokens,
        "cache_read_input_tokens": record.cache_read_input_tokens,
        "cache_creation_input_tokens": record.cache_creation_input_tokens,
        "metadata": _safe_json(record.metadata),
    }


def _record_from_json(payload: str | None) -> ProviderCacheRecord | None:
    if payload is None:
        return None
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    return ProviderCacheRecord(
        provider=str(raw.get("provider") or ""),
        model=str(raw.get("model") or ""),
        key=raw.get("key") if isinstance(raw.get("key"), str) else None,
        provider_cache_id=raw.get("provider_cache_id")
        if isinstance(raw.get("provider_cache_id"), str)
        else None,
        strategy=str(raw.get("strategy") or "provider_managed"),
        ttl=raw.get("ttl") if isinstance(raw.get("ttl"), str) else None,
        created_at=_parse_datetime(raw.get("created_at")) or datetime.now(UTC),
        expires_at=_parse_datetime(raw.get("expires_at")),
        last_used_at=_parse_datetime(raw.get("last_used_at")),
        hits=_int(raw.get("hits")),
        misses=_int(raw.get("misses")),
        input_tokens=_int(raw.get("input_tokens")),
        cached_input_tokens=_int(raw.get("cached_input_tokens")),
        cache_read_input_tokens=_int(raw.get("cache_read_input_tokens")),
        cache_creation_input_tokens=_int(raw.get("cache_creation_input_tokens")),
        metadata=dict(raw.get("metadata") or {}),
        id=str(raw.get("id") or f"cache_{uuid4().hex}"),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _safe_json(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_safe_json(item) for item in value]
    return repr(value)


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_caches (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    key TEXT,
    provider_cache_id TEXT,
    expires_at TEXT,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_caches_key
ON provider_caches(provider, model, key);
CREATE INDEX IF NOT EXISTS idx_provider_caches_provider_cache_id
ON provider_caches(provider, provider_cache_id);
"""
