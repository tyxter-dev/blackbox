# ADR-0001 — No LiteLLM; in-house ProviderRegistry

- **Status:** Accepted
- **Recorded:** 2026-06-24 (decision made during 2026-04/05 design)
- **Sources:** PRD §3.3, §7.3; `AGENTS.md` → Hard constraints

## Context

Provider routing and normalization is a common need, and LiteLLM is the obvious
off-the-shelf dependency. But LiteLLM normalizes providers down to a shared
chat/text format. That erases exactly what blackbox's supervision layer depends
on: provider-native events, continuation state, hosted tools, reasoning items,
and capability differences.

## Decision

No LiteLLM dependency, ever. Provider routing is a tiny in-house
`ProviderRegistry`. Each provider is an adapter that preserves native semantics
rather than collapsing them.

## Consequences

- Provider-native power (events, state, hosted tools, capabilities) is preserved.
- Zero coupling to LiteLLM's release cadence or data model.
- Blackbox owns the adapter maintenance treadmill (accepted — see the
  `ROADMAP.md` strategic thesis: keep model adapters healthy, don't chase
  breadth).
- Routing, capability negotiation, and pricing/catalog handling must be
  implemented in-house.

## Alternatives considered

- **LiteLLM** — rejected: lowest-common-denominator chat normalization.
- **Other multi-provider routers** — rejected for the same reason.
