from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class ModelUsage:
    """Normalized token accounting for one or more model turns."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }

    def add(self, other: ModelUsage) -> ModelUsage:
        return ModelUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


@dataclass(slots=True, frozen=True)
class ModelPricing:
    """Per-million-token pricing for cost estimates."""

    provider: str
    model: str
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None
    currency: str = "USD"

    def estimate(self, usage: ModelUsage) -> dict[str, Any]:
        cached = usage.cached_input_tokens
        billable_input = max(usage.input_tokens - cached, 0)
        input_cost = billable_input * self.input_per_million / 1_000_000
        cached_cost = (
            cached * self.cached_input_per_million / 1_000_000
            if self.cached_input_per_million is not None
            else cached * self.input_per_million / 1_000_000
        )
        output_cost = usage.output_tokens * self.output_per_million / 1_000_000
        return {
            "provider": self.provider,
            "model": self.model,
            "currency": self.currency,
            "input": input_cost,
            "cached_input": cached_cost,
            "output": output_cost,
            "total": input_cost + cached_cost + output_cost,
        }


@dataclass(slots=True)
class ModelCatalog:
    """In-memory model pricing catalog.

    The runtime does not ship hard-coded prices because provider pricing changes
    often. Applications can register current prices and receive cost estimates
    in result metadata when provider usage is available.
    """

    _pricing: dict[tuple[str, str], ModelPricing] = field(default_factory=dict)

    def register_pricing(self, pricing: ModelPricing) -> None:
        self._pricing[(pricing.provider, pricing.model)] = pricing

    def estimate_cost(
        self, *, provider: str, model: str, usage: ModelUsage | dict[str, Any]
    ) -> dict[str, Any] | None:
        pricing = self._pricing.get((provider, model))
        if pricing is None:
            return None
        normalized = usage if isinstance(usage, ModelUsage) else usage_from_mapping(usage)
        return pricing.estimate(normalized)


def usage_from_mapping(value: dict[str, Any] | None) -> ModelUsage:
    if not value:
        return ModelUsage()
    return ModelUsage(
        input_tokens=_int(value.get("input_tokens")),
        output_tokens=_int(value.get("output_tokens")),
        total_tokens=_int(value.get("total_tokens")),
        cached_input_tokens=_int(value.get("cached_input_tokens")),
        reasoning_tokens=_int(value.get("reasoning_tokens")),
    )


def usage_to_dict(usage: ModelUsage | dict[str, Any] | None) -> dict[str, int] | None:
    if usage is None:
        return None
    if isinstance(usage, ModelUsage):
        return usage.to_dict()
    return usage_from_mapping(usage).to_dict()


def add_usage(
    left: ModelUsage | dict[str, Any] | None,
    right: ModelUsage | dict[str, Any] | None,
) -> ModelUsage | None:
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
        reasoning_tokens=_int(_attr(output_details, "reasoning_tokens")),
    )


def usage_from_anthropic_message(message: Any) -> ModelUsage | None:
    usage = _attr(message, "usage")
    if usage is None:
        return None
    input_tokens = _int(_attr(usage, "input_tokens"))
    output_tokens = _int(_attr(usage, "output_tokens"))
    cached = _int(_attr(usage, "cache_read_input_tokens")) + _int(
        _attr(usage, "cache_creation_input_tokens")
    )
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cached_input_tokens=cached,
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
        reasoning_tokens=_int(_attr(usage, "thoughts_token_count")),
    )


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0
