"""Multi-tenant provider pattern: a cached runtime per tenant.

Runnable offline version of the recipe in ``docs/MULTITENANCY.md``: each
tenant gets its own ``AgentRuntime`` from an LRU-cached factory carrying its
own credentials, provider fleet, and resale markup, while all tenants share
one durable run store. Per-tenant fallback chains are built from whichever
"keys" the tenant has.

Tenant B's primary provider is down, so its run fails over to its second
provider; tenant A's primary works first try. Both land in the shared store.

Run::

    python examples/multi_tenant_runtimes.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

from _bootstrap import bootstrap

bootstrap()

from blackbox import AgentRuntime, MarkupPolicy, ModelPricing
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.errors import ProviderExecutionError
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.state import ProviderState, RunState
from blackbox.core.stores import SQLiteRunStore
from blackbox.providers.base import TurnRequest

DB_PATH = Path(__file__).resolve().parent / "state" / "tenants.sqlite"

# Application-owned credential resolution (normally a vault/database).
TENANT_KEYS = {
    "tenant-bella-cucina": {"alpha": "key-bella-alpha", "beta": "key-bella-beta", "markup": 1.5},
    "tenant-prado-arq": {"alpha": None, "beta": "key-prado-beta", "markup": 2.0},
}

ALPHA_DOWN = True  # simulate a provider outage for the demo


class FakeProvider:
    """Stands in for a real adapter: constructed with one tenant's key."""

    def __init__(self, provider_id: str, api_key: str, *, down: bool = False) -> None:
        self.provider_id = provider_id
        self._api_key = api_key
        self._down = down

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        if self._down:
            raise ProviderExecutionError(f"{self.provider_id} is unavailable")
        reply = f"[{self.provider_id} using {self._api_key}] handled: {request.input}"
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider=self.provider_id)
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA, provider=self.provider_id, data={"delta": reply}
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider=self.provider_id,
            data={
                "model": request.model,
                "provider_state": ProviderState(provider=self.provider_id),
                "usage": {"input_tokens": 12_000, "output_tokens": 600},
            },
        )


# Shared durable infrastructure (fresh file per demo run).
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
if DB_PATH.exists():
    DB_PATH.unlink()
RUN_STORE = SQLiteRunStore(DB_PATH)


@lru_cache(maxsize=64)
def runtime_for_tenant(tenant_id: str) -> AgentRuntime:
    keys = TENANT_KEYS[tenant_id]
    runtime = AgentRuntime(run_store=RUN_STORE)
    if keys["alpha"]:
        runtime.registry.register_model(
            FakeProvider("alpha", str(keys["alpha"]), down=ALPHA_DOWN)
        )
    if keys["beta"]:
        runtime.registry.register_model(FakeProvider("beta", str(keys["beta"])))
    for provider_id in ("alpha", "beta"):
        runtime.model_catalog.register_pricing(ModelPricing(
            provider=provider_id, model="support-llm",
            input_per_million=1.00, output_per_million=4.00,
        ))
    runtime.model_catalog.register_billing_policy(
        MarkupPolicy(multiplier=float(keys["markup"]), minimum_charge=0.001)
    )
    return runtime


def fallback_chain(tenant_id: str) -> list[str]:
    keys = TENANT_KEYS[tenant_id]
    return [f"{p}:support-llm" for p in ("alpha", "beta") if keys.get(p)]


async def handle_message(tenant_id: str, message: str) -> None:
    runtime = runtime_for_tenant(tenant_id)
    chain = fallback_chain(tenant_id)
    result = await runtime.run(
        input=message,
        provider=chain[0],
        fallback_providers=chain[1:],
    )
    accounting = result.metadata["accounting"]
    fallback = result.metadata.get("fallback", {})
    await runtime.run_store.save(RunState(
        provider=fallback.get("provider_used", chain[0]),
        model="support-llm",
        provider_state=result.provider_state,
        metadata={"tenant_id": tenant_id},
    ))
    print(f"{tenant_id}:")
    print(f"  chain: {chain}  used: {fallback.get('provider_used', chain[0])}")
    for attempt in fallback.get("attempts", []):
        print(f"  failover: {attempt['provider']} -> {attempt.get('error', attempt.get('skipped'))}")
    print(f"  reply: {result.text}")
    print(f"  billable (markup x{TENANT_KEYS[tenant_id]['markup']}): "
          f"{accounting['billable']['total']:.6f} USD\n")


async def main() -> None:
    await handle_message("tenant-prado-arq", "Qualify this new lead, please.")
    await handle_message("tenant-bella-cucina", "Book a table for two at 20:00.")
    # The cached factory means repeated messages reuse the tenant runtime.
    assert runtime_for_tenant("tenant-bella-cucina") is runtime_for_tenant("tenant-bella-cucina")
    await RUN_STORE.close()


if __name__ == "__main__":
    asyncio.run(main())
