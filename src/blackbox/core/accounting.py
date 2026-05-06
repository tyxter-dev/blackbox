from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

PriceKind = Literal["provider_cost", "billable"]


@dataclass(slots=True, frozen=True)
class ModelUsage:
    """Normalized token accounting for one or more model turns."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    reasoning_tokens: int = 0
    tool_calls: int = 0
    provider_details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        split_cached = self.cache_read_input_tokens + self.cache_creation_input_tokens
        if self.cached_input_tokens == 0 and split_cached:
            object.__setattr__(self, "cached_input_tokens", split_cached)

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "tool_calls": self.tool_calls,
        }

    def add(self, other: ModelUsage) -> ModelUsage:
        return ModelUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens
            + other.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            tool_calls=self.tool_calls + other.tool_calls,
            provider_details=_merge_provider_details(
                self.provider_details,
                other.provider_details,
            ),
        )


@dataclass(slots=True, frozen=True)
class ModelPricing:
    """Per-million-token pricing for cost estimates."""

    provider: str
    model: str
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None
    cache_read_input_per_million: float | None = None
    cache_creation_input_per_million: float | None = None
    reasoning_output_per_million: float | None = None
    currency: str = "USD"
    source: str = "user"
    catalog_version: str | None = None
    effective_at: str | None = None
    retrieved_at: str | None = None
    source_url: str | None = None

    def estimate(
        self,
        usage: ModelUsage,
        *,
        kind: PriceKind = "provider_cost",
    ) -> dict[str, Any]:
        cache_read = usage.cache_read_input_tokens
        cache_creation = usage.cache_creation_input_tokens
        split_cached = cache_read + cache_creation
        legacy_cached = max(usage.cached_input_tokens - split_cached, 0)
        cached = usage.cached_input_tokens
        billable_input = max(usage.input_tokens - cached, 0)
        input_cost = billable_input * self.input_per_million / 1_000_000
        cache_read_rate = _rate(
            self.cache_read_input_per_million,
            self.cached_input_per_million,
            self.input_per_million,
        )
        cache_creation_rate = _rate(
            self.cache_creation_input_per_million,
            self.cached_input_per_million,
            self.input_per_million,
        )
        cache_read_cost = (cache_read + legacy_cached) * cache_read_rate / 1_000_000
        cache_creation_cost = cache_creation * cache_creation_rate / 1_000_000
        output_cost = usage.output_tokens * self.output_per_million / 1_000_000
        reasoning_cost = (
            usage.reasoning_tokens * self.reasoning_output_per_million / 1_000_000
            if self.reasoning_output_per_million is not None
            else 0.0
        )
        line_items = [
            _line_item(
                name="input",
                quantity=billable_input,
                rate_per_million=self.input_per_million,
                amount=input_cost,
            ),
            _line_item(
                name="cache_read_input",
                quantity=cache_read + legacy_cached,
                rate_per_million=cache_read_rate,
                amount=cache_read_cost,
            ),
            _line_item(
                name="cache_creation_input",
                quantity=cache_creation,
                rate_per_million=cache_creation_rate,
                amount=cache_creation_cost,
            ),
            _line_item(
                name="output",
                quantity=usage.output_tokens,
                rate_per_million=self.output_per_million,
                amount=output_cost,
            ),
        ]
        if self.reasoning_output_per_million is not None:
            line_items.append(
                _line_item(
                    name="reasoning_output",
                    quantity=usage.reasoning_tokens,
                    rate_per_million=self.reasoning_output_per_million,
                    amount=reasoning_cost,
                )
            )
        estimate: dict[str, Any] = {
            "kind": kind,
            "provider": self.provider,
            "model": self.model,
            "currency": self.currency,
            "source": self.source,
            "input": input_cost,
            "cached_input": cache_read_cost + cache_creation_cost,
            "cache_read_input": cache_read_cost,
            "cache_creation_input": cache_creation_cost,
            "output": output_cost,
            "reasoning_output": reasoning_cost,
            "total": input_cost + cache_read_cost + cache_creation_cost + output_cost
            + reasoning_cost,
            "line_items": [item for item in line_items if item["quantity"] > 0],
        }
        _attach_pricing_source(estimate, self)
        return estimate


@dataclass(slots=True, frozen=True)
class MarkupPolicy:
    """Billable pricing policy derived from provider cost."""

    multiplier: float = 1.0
    fixed_per_run: float = 0.0
    minimum_charge: float | None = None
    round_to: str | None = None
    currency: str | None = None
    source: str = "user"
    name: str = "markup"

    def apply(self, provider_cost: dict[str, Any]) -> dict[str, Any]:
        base_total = _float(provider_cost.get("total"))
        subtotal = base_total * self.multiplier + self.fixed_per_run
        minimum_adjustment = 0.0
        if self.minimum_charge is not None and subtotal < self.minimum_charge:
            minimum_adjustment = self.minimum_charge - subtotal
            subtotal = self.minimum_charge
        total = _round_amount(subtotal, self.round_to)
        currency = str(provider_cost.get("currency") or self.currency or "USD")
        line_items = [
            {
                "name": "provider_cost",
                "amount": base_total,
                "currency": currency,
            }
        ]
        markup_amount = base_total * (self.multiplier - 1.0)
        if markup_amount:
            line_items.append(
                {
                    "name": "markup",
                    "multiplier": self.multiplier,
                    "amount": markup_amount,
                    "currency": currency,
                }
            )
        if self.fixed_per_run:
            line_items.append(
                {
                    "name": "fixed_per_run",
                    "amount": self.fixed_per_run,
                    "currency": currency,
                }
            )
        if minimum_adjustment:
            line_items.append(
                {
                    "name": "minimum_charge_adjustment",
                    "amount": minimum_adjustment,
                    "currency": currency,
                }
            )
        return {
            "kind": "billable",
            "provider": provider_cost.get("provider"),
            "model": provider_cost.get("model"),
            "currency": self.currency or currency,
            "source": self.source,
            "pricing_policy": self.name,
            "provider_cost_total": base_total,
            "multiplier": self.multiplier,
            "fixed_per_run": self.fixed_per_run,
            "minimum_charge": self.minimum_charge,
            "round_to": self.round_to,
            "total": total,
            "line_items": line_items,
        }


@dataclass(slots=True)
class ModelCatalog:
    """In-memory model pricing catalog.

    The runtime does not ship hard-coded prices because provider pricing changes
    often. Applications can register current prices and receive cost estimates
    in result metadata when provider usage is available.
    """

    _pricing: dict[tuple[str, str], ModelPricing] = field(default_factory=dict)
    _billable_pricing: dict[tuple[str, str], ModelPricing] = field(default_factory=dict)
    billing_policy: MarkupPolicy | None = None

    def register_pricing(self, pricing: ModelPricing) -> None:
        self.register_provider_pricing(pricing)

    def register_provider_pricing(self, pricing: ModelPricing) -> None:
        self._pricing[(pricing.provider, pricing.model)] = pricing

    def register_billable_pricing(self, pricing: ModelPricing) -> None:
        self._billable_pricing[(pricing.provider, pricing.model)] = pricing

    def register_many(
        self,
        pricing: list[ModelPricing],
        *,
        kind: PriceKind = "provider_cost",
    ) -> None:
        for item in pricing:
            if kind == "billable":
                self.register_billable_pricing(item)
            else:
                self.register_provider_pricing(item)

    def register_billing_policy(self, policy: MarkupPolicy | None) -> None:
        self.billing_policy = policy

    def estimate_cost(
        self, *, provider: str, model: str, usage: ModelUsage | dict[str, Any]
    ) -> dict[str, Any] | None:
        return self.estimate_provider_cost(provider=provider, model=model, usage=usage)

    def estimate_provider_cost(
        self, *, provider: str, model: str, usage: ModelUsage | dict[str, Any]
    ) -> dict[str, Any] | None:
        pricing = self._pricing.get((provider, model))
        if pricing is None:
            return None
        normalized = usage if isinstance(usage, ModelUsage) else usage_from_mapping(usage)
        return pricing.estimate(normalized, kind="provider_cost")

    def estimate_billable(
        self,
        *,
        provider: str,
        model: str,
        usage: ModelUsage | dict[str, Any],
        provider_cost: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        normalized = usage if isinstance(usage, ModelUsage) else usage_from_mapping(usage)
        billable_pricing = self._billable_pricing.get((provider, model))
        if billable_pricing is not None:
            return billable_pricing.estimate(normalized, kind="billable")
        if self.billing_policy is None:
            return None
        effective_provider_cost = provider_cost or self.estimate_provider_cost(
            provider=provider,
            model=model,
            usage=normalized,
        )
        if effective_provider_cost is None:
            return None
        return self.billing_policy.apply(effective_provider_cost)


def usage_from_mapping(value: dict[str, Any] | None) -> ModelUsage:
    if not value:
        return ModelUsage()
    return ModelUsage(
        input_tokens=_int(value.get("input_tokens")),
        output_tokens=_int(value.get("output_tokens")),
        total_tokens=_int(value.get("total_tokens")),
        cached_input_tokens=_int(value.get("cached_input_tokens")),
        cache_read_input_tokens=_int(value.get("cache_read_input_tokens")),
        cache_creation_input_tokens=_int(value.get("cache_creation_input_tokens")),
        reasoning_tokens=_int(value.get("reasoning_tokens")),
        tool_calls=_int(value.get("tool_calls")),
        provider_details=dict(value.get("provider_details", {}))
        if isinstance(value.get("provider_details"), dict)
        else {},
    )


def usage_to_dict(usage: ModelUsage | dict[str, Any] | None) -> dict[str, int] | None:
    if usage is None:
        return None
    if isinstance(usage, ModelUsage):
        return usage.to_dict()
    return usage_from_mapping(usage).to_dict()


def usage_provider_details(
    usage: ModelUsage | dict[str, Any] | None,
) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, ModelUsage):
        return usage.provider_details or None
    details = usage.get("provider_details")
    return details if isinstance(details, dict) and details else None


def add_usage(
    left: ModelUsage | dict[str, Any] | None,
    right: ModelUsage | dict[str, Any] | None,
) -> ModelUsage | None:
    if left is None and right is None:
        return None
    if left is None:
        return right if isinstance(right, ModelUsage) else usage_from_mapping(right)
    if right is None:
        return left if isinstance(left, ModelUsage) else usage_from_mapping(left)
    left_usage = left if isinstance(left, ModelUsage) else usage_from_mapping(left)
    right_usage = right if isinstance(right, ModelUsage) else usage_from_mapping(right)
    return left_usage.add(right_usage)


def usage_from_openai_response(response: Any) -> ModelUsage | None:
    usage = _attr(response, "usage")
    if usage is None:
        return None
    input_details = _attr(usage, "input_tokens_details")
    output_details = _attr(usage, "output_tokens_details")
    return ModelUsage(
        input_tokens=_int(_attr(usage, "input_tokens")),
        output_tokens=_int(_attr(usage, "output_tokens")),
        total_tokens=_int(_attr(usage, "total_tokens")),
        cached_input_tokens=_int(_attr(input_details, "cached_tokens")),
        cache_read_input_tokens=_int(_attr(input_details, "cached_tokens")),
        reasoning_tokens=_int(_attr(output_details, "reasoning_tokens")),
        provider_details=_to_plain(usage),
    )


def usage_from_anthropic_message(message: Any) -> ModelUsage | None:
    usage = _attr(message, "usage")
    if usage is None:
        return None
    input_tokens = _int(_attr(usage, "input_tokens"))
    output_tokens = _int(_attr(usage, "output_tokens"))
    cache_read = _int(_attr(usage, "cache_read_input_tokens"))
    cache_creation = _int(_attr(usage, "cache_creation_input_tokens"))
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cached_input_tokens=cache_read + cache_creation,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
        provider_details=_to_plain(usage),
    )


def usage_from_gemini_chunk(chunk: Any) -> ModelUsage | None:
    usage = _attr(chunk, "usage_metadata")
    if usage is None:
        return None
    return ModelUsage(
        input_tokens=_int(_attr(usage, "prompt_token_count")),
        output_tokens=_int(_attr(usage, "candidates_token_count")),
        total_tokens=_int(_attr(usage, "total_token_count")),
        cached_input_tokens=_int(_attr(usage, "cached_content_token_count")),
        cache_read_input_tokens=_int(_attr(usage, "cached_content_token_count")),
        reasoning_tokens=_int(_attr(usage, "thoughts_token_count")),
        provider_details=_to_plain(usage),
    )


def _rate(*values: float | None) -> float:
    for value in values:
        if value is not None:
            return value
    return 0.0


def _line_item(
    *,
    name: str,
    quantity: int,
    rate_per_million: float,
    amount: float,
) -> dict[str, Any]:
    return {
        "name": name,
        "quantity": quantity,
        "unit": "tokens",
        "rate_per_million": rate_per_million,
        "amount": amount,
    }


def _attach_pricing_source(
    estimate: dict[str, Any],
    pricing: ModelPricing,
) -> None:
    optional_fields = {
        "catalog_version": pricing.catalog_version,
        "effective_at": pricing.effective_at,
        "retrieved_at": pricing.retrieved_at,
        "source_url": pricing.source_url,
    }
    for key, value in optional_fields.items():
        if value is not None:
            estimate[key] = value


def _float(value: Any) -> float:
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    return 0.0


def _round_amount(value: float, quantum: str | None) -> float:
    if quantum is None:
        return value
    rounded = Decimal(str(value)).quantize(
        Decimal(quantum),
        rounding=ROUND_HALF_UP,
    )
    return float(rounded)


def _merge_provider_details(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if not left:
        return dict(right)
    if not right:
        return dict(left)
    return {"turns": [*_detail_turns(left), *_detail_turns(right)]}


def _detail_turns(value: dict[str, Any]) -> list[dict[str, Any]]:
    turns = value.get("turns")
    if isinstance(turns, list) and all(isinstance(turn, dict) for turn in turns):
        return [dict(turn) for turn in turns]
    return [dict(value)]


def _to_plain(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {
            str(key): _plain_value(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {}


def _plain_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    if isinstance(value, tuple):
        return [_plain_value(item) for item in value]
    if hasattr(value, "__dict__"):
        return _to_plain(value)
    return repr(value)


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0
