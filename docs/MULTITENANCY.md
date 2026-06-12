# Multi-Tenant Provider Pattern

Provider adapters take credentials at construction (`OpenAIResponsesProvider(
api_key=...)`), while platforms resolve credentials per tenant at request
time. This document is the recommended recipe for bridging the two. Key
storage, secret vaults, and key-resolution policy stay downstream — blackbox
only defines where tenant-scoped objects plug in.

## Recommended recipe: a runtime per tenant

Build an `AgentRuntime` per tenant from a factory, cache it (LRU or TTL), and
share the durable infrastructure by passing the *same store instances* into
every runtime:

```python
from functools import lru_cache

from blackbox import AgentRuntime, MarkupPolicy, ModelPricing
from blackbox.providers.model_adapters.openai_responses import OpenAIResponsesProvider
from blackbox.providers.model_adapters.xai_responses import XAIResponsesProvider

# Shared, process-wide durable infrastructure.
EVENT_STORE = JSONLEventStore("./events.jsonl")
RUN_STORE = SQLiteRunStore("./runs.sqlite")


@lru_cache(maxsize=512)
def runtime_for_tenant(tenant_id: str) -> AgentRuntime:
    keys = resolve_tenant_keys(tenant_id)          # application-owned
    runtime = AgentRuntime(event_store=EVENT_STORE, run_store=RUN_STORE)
    if keys.openai:
        runtime.registry.register_model(OpenAIResponsesProvider(api_key=keys.openai))
    if keys.xai:
        runtime.registry.register_model(XAIResponsesProvider(api_key=keys.xai))
    # Tenant-specific resale pricing rides on the tenant's runtime.
    runtime.model_catalog.register_billing_policy(
        MarkupPolicy(multiplier=keys.markup, minimum_charge=0.001)
    )
    return runtime
```

Why this is the default recommendation:

- **Isolation is structural.** A tenant's credentials, pricing overrides,
  billing policy, and tool registrations live on the tenant's runtime;
  nothing leaks through a shared registry.
- **Construction is cheap.** `AgentRuntime()` measures ~0.1 ms including
  bundled catalog seeding. The real per-tenant cost is provider SDK client
  construction (HTTP connection pools) — which is exactly why the factory is
  cached rather than rebuilt per request.
- **Durable state stays shared.** `event_store`, `run_store`, session and
  provider-cache stores are constructor arguments; pass shared instances and
  every tenant runtime reads/writes the same persistence layer. Stamp tenant
  ids into run/session metadata for partitioning.

Per-tenant fallback chains compose naturally — build the candidate list from
whichever keys the tenant has:

```python
candidates = [ref for ref in ("openai:gpt-5.4", "xai:grok-4") if tenant_has_key(ref)]
result = await runtime_for_tenant(tid).run(
    input=message,
    provider=candidates[0],
    fallback_providers=candidates[1:],
)
```

Invalidate the cache entry when a tenant rotates keys
(`runtime_for_tenant.cache_clear()` or a keyed TTL cache).

## Alternative: one shared runtime with provider aliases

`ProviderRegistry.register_model(provider, *aliases)` accepts aliases, so a
single runtime can host per-tenant providers:

```python
runtime.registry.register_model(
    OpenAIResponsesProvider(api_key=key_a), f"openai@{tenant_a}"
)
result = await runtime.run(provider=f"openai@{tenant_a}:gpt-5.4", input=...)
```

Caveats that make this the secondary option:

- The provider's own `provider_id` (`"openai"`) is *also* registered each
  time, so the bare key silently points at the most recently registered
  tenant's client — a cross-tenant footgun if any caller uses the bare ref.
- Pricing/billing catalogs and tool registries are runtime-wide, so
  per-tenant markup or tool differences need name discipline.

Use it only when tenants differ *exclusively* by credential and a single
runtime is a hard requirement.

## What stays downstream

Key storage and rotation, secret vaults, OAuth flows, per-tenant quotas and
rate limits, and tenant authentication. Blackbox's contribution is that every
tenant-scoped concern has a constructor argument or registry slot to plug
into; see `examples/multi_tenant_runtimes.py` for a runnable offline version
of the recommended recipe.
