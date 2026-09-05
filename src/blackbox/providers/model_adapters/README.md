---
status: active
owner: blackbox-src
since: 2026-06-27
adr:
  - docs/adr/0001-no-litellm-in-house-registry.md
  - docs/adr/0002-chat-messages-are-a-projection.md
  - docs/adr/0004-preserve-raw-provider-payloads.md
  - docs/adr/0005-provider-native-provider-state.md
prd: docs/prd/03-model-providers.md
---

# providers.model_adapters

`providers.model_adapters` contains `ModelProvider` implementations. Each
module maps one provider's model-turn API into the common runtime contract while
preserving provider-native state, event semantics, tools, controls, usage, and
raw payloads.

## Belongs Here

- Direct model-turn adapters.
- Provider-native request mapping.
- Provider-native streaming event mapping.
- Model-specific capability profiles and validation helpers.
- Deterministic/test model providers.

## Does Not Belong Here

- Agent-session providers.
- Runtime loop orchestration.
- Local tool execution.
- Workspace backend behavior.

## Compatibility

`blackbox.models` remains as a compatibility package for older imports.

## Model snapshot and current controls

The 2026-09-05 catalog adds GPT-6 Astra, GPT-5.6 Sol/Terra/Luna, Claude
Fable 5.1/5, Opus 5/4.8, Sonnet 5, and Grok 4.6. The `gpt-5.6` alias resolves
to Sol for catalog lookup and pricing. Existing application defaults are unchanged.
Astra access depends on the account; catalog presence does not guarantee availability.
Retired Claude and Grok identifiers remain audit records; replacement metadata
does not rewrite dispatched model IDs. Unchanged rows retain their earlier
retrieval dates, including Google rows.

Sources retrieved 2026-09-05: [OpenAI model pages](https://developers.openai.com/api/docs/models/gpt-6-astra),
[Claude model overview](https://platform.claude.com/docs/en/models/overview),
[Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing),
[Claude retirements](https://platform.claude.com/docs/en/about-claude/model-deprecations),
[Grok 4.6](https://docs.x.ai/developers/models/grok-4.6), and
[Grok 4.3](https://docs.x.ai/developers/models/grok-4.3). Individual rows retain
model-specific source URLs where available.

The added Claude models map reasoning effort to adaptive thinking and
`output_config.effort`, preserving native structured-output format. They reject
manual thinking budgets and nondefault sampling before SDK dispatch; supplied
`top_k` is unsupported by this adapter. Opus 5 rejects WebFetch. Fable 5.1 accepts
only auto/none tool choice, so `finalizer_tool` is unavailable. Its native thinking
blocks, including empty thinking with signatures, are preserved. Replay requires
unchanged system, tools, and prior messages; the adapter records prefix provenance
in `ProviderState.tool_state` and rejects changed prefixes, imported thinking
history without provenance, and replay to a different model. Start a fresh state
when changing those inputs. These restrictions follow the
[Fable 5.1 guidance](https://platform.claude.com/docs/en/models/fable-5-1/overview).
Other model replay behavior is unchanged.

Bundled prices estimate standard token rates, including Fable 5.1's $0.25/M cache
reads and Sonnet 5's permanent $2/M input and $10/M output rates. Claude cache
creation uses the five-minute rate. The simple estimator does not model one-hour
cache creation, long-context premiums, batch/flex/fast or regional tiers, storage,
or per-tool charges. OpenAI Sol/Terra/Luna rates are promotional (at least through
2026-11-21); this dated snapshot does not schedule price changes. User price
catalogs continue to override bundled rates. Grok 4.1-fast audit rows use the
current Grok 4.3 redirect rates; unverified Grok maximum-output limits remain
unknown.

GPT-6 Astra rejects `temperature`, `top_p`, `top_logprobs`, and
`include=["message.output_text.logprobs"]` before SDK dispatch, including native
body overrides. The four new OpenAI IDs and the Sol alias map cache TTL to
`prompt_cache_options.ttl`; only `30m` is supported. Native cache options retain
other fields and override typed TTL; invalid effective values and the legacy
`prompt_cache_retention` field are rejected for these models. The effective
native model determines this mapping; older models retain the retention field.
The locked OpenAI SDK accepts `prompt_cache_options` directly. See the
[OpenAI migration guide](https://developers.openai.com/api/docs/guides/latest-model)
and [prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching),
retrieved 2026-09-05.

OpenAI native `input_tokens_details.cache_write_tokens` becomes normalized
`cache_creation_input_tokens`. Normalized cached input includes reads plus writes,
so pricing subtracts both from the inclusive native input count before charging
ordinary input; native usage details are preserved. Missing write counts remain
zero. This uses the existing per-model cache-write rates, without changing the
standard-tier exclusions above.

Anthropic reports ordinary input separately from cache reads/writes. The adapter
normalizes their sum into inclusive `input_tokens` and adds output for
`total_tokens`, keeping native exclusive counts in `provider_details`. This lets
the same estimator charge ordinary/read/write components once, including across
accumulated turns. See [Claude input accounting](https://platform.claude.com/docs/en/api/rate-limits),
retrieved 2026-09-05.

Runtime cache `hit_ratio` counts cache-read tokens divided by normalized input
tokens. Combined cached tokens are the legacy fallback when both split read/write
counters are zero; cache writes alone do not count as hits. Nonpositive input
counts leave the ratio absent.
