# ModelProvider Usage And Cost Tracking Implementation Plan

Status: implemented, with pricing catalog caveats.

Source of truth: `FEATURES.md` lists the public feature status and
`tests/VALIDATION.md` lists the test coverage. This spec is retained as the
historical implementation plan for usage and cost tracking.

Implemented:

- Split cache read and cache creation token fields.
- Provider-specific usage details in result metadata.
- Expanded cost estimate fields for input, cache read, cache creation, output,
  reasoning output, and total.
- `ModelCatalog.register_many(...)`.
- Unit tests and network-gated OpenAI/Gemini/xAI validation.

Remaining:

- Built-in live pricing defaults are intentionally not shipped because provider
  prices change frequently.
- Budgets/quotas are out of scope.

## Goal

Make usage and cost tracking a first-class ModelProvider feature that can support real benchmarking and cost comparison across providers.

The runtime should:

- preserve normalized usage across all model turns;
- preserve provider-specific usage details;
- split cache read and cache creation tokens where providers expose them;
- emit stable cost metadata when pricing is registered;
- provide a simple pricing bootstrap path without hard-coding volatile defaults.

## Original Current State

The project has a useful foundation:

- `ModelUsage` tracks input, output, total, cached input, and reasoning tokens.
- Provider adapters extract usage from OpenAI, Anthropic, and Gemini responses.
- `ModelCatalog` can register `ModelPricing`.
- `ModelRuntime.run` and `AgentRuntime.run` aggregate usage and add cost metadata when pricing is registered.

Main gaps:

- cache read and cache creation tokens are collapsed into one field;
- provider raw usage is not retained in metadata;
- no standard cost summary object exists across single-turn and agent-loop runs;
- pricing registration is manual and not ergonomic for benchmarks.

## Public API

Extend `ModelUsage` with:

```python
cache_read_input_tokens: int = 0
cache_creation_input_tokens: int = 0
provider_details: dict[str, Any] = {}
```

Keep `cached_input_tokens` as the compatibility total:

```python
cached_input_tokens = cache_read_input_tokens + cache_creation_input_tokens
```

Extend `ModelPricing` with:

```python
cache_read_input_per_million: float | None = None
cache_creation_input_per_million: float | None = None
reasoning_output_per_million: float | None = None
```

Preserve `cached_input_per_million` as a compatibility fallback.

Add `ModelCatalog.register_many(...)` for benchmark setup.

## Runtime Metadata

When usage is available, runtime metadata should contain:

```python
metadata["usage"] = {
  "input_tokens": ...,
  "output_tokens": ...,
  "total_tokens": ...,
  "cached_input_tokens": ...,
  "cache_read_input_tokens": ...,
  "cache_creation_input_tokens": ...,
  "reasoning_tokens": ...
}
```

When provider details are available:

```python
metadata["usage_provider_details"] = {...}
```

When pricing is available:

```python
metadata["cost"] = {
  "provider": "...",
  "model": "...",
  "currency": "USD",
  "input": ...,
  "cache_read_input": ...,
  "cache_creation_input": ...,
  "output": ...,
  "reasoning_output": ...,
  "total": ...
}
```

## Provider Mappings

### OpenAI / xAI

- `input_tokens_details.cached_tokens` maps to `cache_read_input_tokens`.
- `output_tokens_details.reasoning_tokens` maps to `reasoning_tokens`.
- Preserve raw usage fields in `provider_details`.

### Anthropic

- `cache_read_input_tokens` maps directly.
- `cache_creation_input_tokens` maps directly.
- Preserve cache creation breakdowns when present.

### Gemini

- `cached_content_token_count` maps to `cache_read_input_tokens`.
- `thoughts_token_count` maps to `reasoning_tokens`.
- Preserve raw `usage_metadata`.

## Tests

Add focused tests for:

- split cache usage extraction for OpenAI, Anthropic, and Gemini;
- `ModelUsage.add` accumulates split cache fields;
- cost estimation distinguishes billable input, cache read, cache creation, output, and reasoning output;
- runtime metadata includes expanded usage and cost fields.

## Non-Goals

- Shipping live provider price defaults.
- Fetching prices from provider websites.
- Per-user budgets or quota enforcement.
