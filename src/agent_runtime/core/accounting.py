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

    def estimate(self, usage: ModelUsage) -> dict[str, Any]:
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
        return {
            "provider": self.provider,
            "model": self.model,
            "currency": self.currency,
            "input": input_cost,
            "cached_input": cache_read_cost + cache_creation_cost,
            "cache_read_input": cache_read_cost,
            "cache_creation_input": cache_creation_cost,
            "output": output_cost,
            "reasoning_output": reasoning_cost,
            "total": input_cost + cache_read_cost + cache_creation_cost + output_cost
            + reasoning_cost,
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

    def register_many(self, pricing: list[ModelPricing]) -> None:
        for item in pricing:
            self.register_pricing(item)

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
