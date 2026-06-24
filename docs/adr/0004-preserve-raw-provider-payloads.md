# ADR-0004 — Preserve raw provider payloads (provider-native escape hatches)

- **Status:** Accepted
- **Recorded:** 2026-06-24 (decision made during 2026-04/05 design)
- **Sources:** PRD §4.9, §9.1; `AGENTS.md` → Hard constraints; `core/raw.py`

## Context

Any normalization layer risks becoming a lowest-common-denominator that hides
provider strengths. Debugging, provider-specific features, and forward
compatibility all need access to what the SDK actually returned.

## Decision

Every normalized event, artifact, and state object carries the raw provider
payload (`raw=<sdk_object>`) wherever possible. `RawEnvelope` (`core/raw.py`)
wraps payloads when sensitivity tagging matters. Production observability
**redacts raw payloads at the trace layer**, not by stripping them at the source.

## Consequences

- Provider-specific capabilities and new fields are always reachable without an
  adapter change.
- Runs are debuggable against ground truth.
- Raw payloads can be large or sensitive — mitigated by trace-layer redaction
  (`ObservabilityPreset.production(...)`), not by discarding data early.

## Alternatives considered

- **Strip to normalized fields only** — rejected: loses provider power and makes
  the library another lossy wrapper; also forces adapter churn whenever a
  provider adds a field.
