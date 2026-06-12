"""Multi-tenant cost accounting: provider cost vs billable price per run.

Mirrors the platform pattern in ``docs/USE_CASE_VALIDATION.md`` (283 tenants
sharing one agent platform): the application pays the provider for tokens
and resells agent usage to its own tenants. The pricing catalog separates
the two money flows — ``provider_cost`` (your bill, from registered or
bundled rates) and ``billable`` (your customer's bill, from a markup
policy) — both emitted on every run's ``result.metadata``.

Offline: a scripted model reports realistic token usage per run.

Run::

    python examples/tenant_billing.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from _bootstrap import bootstrap

bootstrap()

from blackbox import AgentRuntime, MarkupPolicy, ModelPricing
from blackbox.core.capabilities import ModelCapabilities
from blackbox.core.events import AgentEvent, EventTypes
from blackbox.core.state import ProviderState
from blackbox.providers.base import TurnRequest


class MeteredModel:
    """Offline model that reports scripted token usage per turn."""

    provider_id = "scripted"

    def __init__(self, usage_per_run: list[dict[str, int]]) -> None:
        self._usage = list(usage_per_run)

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        return ModelCapabilities(supports_streaming_events=True)

    async def stream_turn(self, request: TurnRequest) -> AsyncIterator[AgentEvent]:
        usage = self._usage.pop(0)
        yield AgentEvent(type=EventTypes.MODEL_REQUEST_STARTED, provider=self.provider_id)
        yield AgentEvent(
            type=EventTypes.MODEL_TEXT_DELTA, provider=self.provider_id, data={"delta": "done"}
        )
        yield AgentEvent(
            type=EventTypes.MODEL_COMPLETED,
            provider=self.provider_id,
            data={
                "model": request.model,
                "provider_state": ProviderState(provider=self.provider_id),
                "usage": usage,
            },
        )


# (tenant, conversation summary, token usage) - one entry per agent run.
RUNS: list[tuple[str, str, dict[str, int]]] = [
    ("tenant-bella-cucina", "returning guest books a table", {"input_tokens": 41_000, "output_tokens": 1_200}),
    ("tenant-bella-cucina", "menu question with RAG context", {"input_tokens": 78_500, "output_tokens": 2_900}),
    ("tenant-prado-arq", "lead qualification + CRM writes", {"input_tokens": 55_300, "output_tokens": 4_100}),
    ("tenant-prado-arq", "short FAQ answer", {"input_tokens": 6_200, "output_tokens": 380}),
]


async def main() -> None:
    runtime = AgentRuntime()
    runtime.registry.register_model(MeteredModel([usage for _, _, usage in RUNS]))

    # Your negotiated provider rates for the model behind the assistants.
    runtime.model_catalog.register_pricing(ModelPricing(
        provider="scripted",
        model="support-llm",
        input_per_million=1.00,
        output_per_million=4.00,
    ))
    # What your tenants pay: provider cost x1.5, never below a 0.1 cent floor.
    runtime.model_catalog.register_billing_policy(MarkupPolicy(
        multiplier=1.5,
        minimum_charge=0.001,
        round_to="0.000001",
    ))

    totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "tokens": 0, "provider_cost": 0.0, "billable": 0.0}
    )

    print(f"{'tenant':22} {'run':34} {'tokens':>8} {'cost USD':>10} {'billable':>10}")
    print("-" * 90)
    for tenant, summary, _ in RUNS:
        result = await runtime.run(provider="scripted:support-llm", input=summary)
        accounting = result.metadata["accounting"]
        usage = accounting["usage"]
        tokens = usage["input_tokens"] + usage["output_tokens"]
        cost = accounting["provider_cost"]["total"]
        billable = accounting["billable"]["total"]
        print(f"{tenant:22} {summary:34} {tokens:>8,} {cost:>10.6f} {billable:>10.6f}")

        bucket = totals[tenant]
        bucket["runs"] += 1
        bucket["tokens"] += tokens
        bucket["provider_cost"] += cost
        bucket["billable"] += billable

    print("-" * 90)
    print("per-tenant invoice rollup:")
    for tenant, bucket in totals.items():
        margin = bucket["billable"] - bucket["provider_cost"]
        print(f"  {tenant:22} {bucket['runs']} runs, {bucket['tokens']:,} tokens | "
              f"provider {bucket['provider_cost']:.6f} | "
              f"billable {bucket['billable']:.6f} | margin {margin:.6f}")

    print("\nnote: provider_cost comes from registered (or bundled) rates and is an "
          "estimate, not an invoice; billable applies your markup policy on top.")


if __name__ == "__main__":
    asyncio.run(main())
