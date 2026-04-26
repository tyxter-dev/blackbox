# ModelProvider Native Caching Implementation Plan

Status: partially implemented.

Source of truth: `FEATURES.md` lists the public feature status and
`tests/VALIDATION.md` lists the test coverage. This spec is retained as the
forward plan for the remaining cache lifecycle work.

Implemented:

- `ModelCacheControl` on `ModelRequestControls`.
- `cache=...` on `ModelRuntime` and `AgentRuntime`.
- OpenAI/xAI prompt cache key and retention request mapping.
- Anthropic ephemeral cache-control mapping for system/input content.
- Gemini cached-content reference mapping.
- Usage/cost metadata reports provider cache usage when providers return it.
- Network-gated cache/cost integration tests.

Remaining:

- Gemini cached-content creation/list/delete APIs.
- Provider-side cache eviction/invalidation.
- Persistent runtime cache registry.
- Dedicated `metadata["cache"]`; cache usage currently lives under
  `metadata["usage"]`, `metadata["usage_provider_details"]`, and
  `metadata["cost"]`.

## Goal

Expose provider-native prompt/context caching as a typed ModelProvider surface without forcing every provider into the same cache model.

The runtime should let applications:

- request cache behavior explicitly on a model turn;
- mark cacheable prompt/input sections when the provider supports that;
- pass provider cache identifiers such as OpenAI prompt cache keys or Gemini cached content names;
- receive normalized cache usage metadata from provider responses;
- keep provider-specific escape hatches available through `extra`.

## Original Current State

The project currently records cache usage only after the provider reports it:

- `ModelUsage.cached_input_tokens` exists.
- OpenAI, Anthropic, and Gemini adapters map provider usage into `cached_input_tokens`.
- Runtime metadata includes aggregated `usage`.
- Provider request controls do not have cache policy fields.
- `_cache_sections` is stripped as a private field before provider calls, so cacheable input sections are not acted on.

## Public API

Add a typed cache policy in `providers/base.py`:

```python
ModelCacheControl(
    strategy="auto" | "ephemeral" | "provider_managed" | "bypass",
    key=None,
    ttl=None,
    cached_content=None,
    breakpoints=[],
    extra={},
)
```

Add it to `ModelRequestControls` as:

```python
cache: ModelCacheControl | None = None
```

Expose `cache=...` on `ModelRuntime.stream`, `ModelRuntime.run`, `AgentRuntime.stream`, and `AgentRuntime.run`.

## Provider Mappings

### OpenAI Responses / xAI Responses

- `cache.key` maps to `prompt_cache_key`.
- `cache.ttl` maps to `prompt_cache_retention` when supplied.
- `strategy="bypass"` raises `UnsupportedFeatureError` because OpenAI prompt caching is provider-managed rather than explicitly disabled in this adapter.
- Cache section markers remain a no-op for OpenAI unless passed by `extra`.
- xAI inherits the same OpenAI-compatible mapping.

### Anthropic Messages

- `strategy="ephemeral"` applies `cache_control={"type": "ephemeral"}` to the last cacheable content block.
- `ttl` maps to `cache_control["ttl"]` when provided.
- `breakpoints` may target `system`, `tools`, or message content blocks in later iterations.
- First implementation applies cache control to:
  - system instructions when present;
  - the last text block in string input;
  - dict input blocks that carry private `_cache_control` or `_cache_sections` metadata.
- `strategy="bypass"` omits cache controls.

### Gemini GenerateContent

- `cache.cached_content` maps to `config.cached_content`.
- `strategy="provider_managed"` allows implicit caching and does not alter request payloads.
- `strategy="bypass"` raises `UnsupportedFeatureError` if the provider has no supported bypass field.
- Cache creation APIs are deferred; this step consumes existing cached content names.

## Events And Metadata

Do not add new events in the first implementation. Instead:

- continue reporting usage under `metadata["usage"]`;
- add `metadata["cache"]` with normalized cache fields when available:
  - `cached_input_tokens`
  - `cache_read_input_tokens`
  - `cache_creation_input_tokens`
  - `cache_key`
  - `cached_content`

## Tests

Add focused unit tests for:

- OpenAI request kwargs include `prompt_cache_key` and `prompt_cache_retention`.
- Anthropic string input/system messages receive `cache_control`.
- Gemini request kwargs include `config.cached_content`.
- Runtime accepts `cache=` and forwards it into `TurnRequest.controls.cache`.
- Unsupported `bypass` behavior raises clearly where applicable.

## Non-Goals

- Creating Gemini cached content resources.
- Provider-side cache eviction.
- Persistent cache stores.
- Pricing table updates.
