# ADR-0007 — Four structured-output strategies; fail-fast validation default

- **Status:** Accepted
- **Recorded:** 2026-06-24 (decision made during 2026-04/05 design)
- **Sources:** PRD §10.7, §26 Q9/Q10; `ROADMAP.md` decision log #9/#10

## Context

Providers differ in structured-output support. Sometimes the provider enforces a
strict schema natively; sometimes the cleanest path is a hidden finalizer tool;
sometimes the only option is parsing the final text. Callers also disagree on
whether validation failure should auto-retry or fail fast.

## Decision

`OutputSpec` exposes four strategies, selected per run:

- `provider_native` — wire the schema into the provider (e.g. OpenAI Responses
  `text.format`).
- `finalizer_tool` — expose a hidden `submit_final_output` tool validated against
  the schema.
- `posthoc_parse` — **default** — parse the final text after generation.
- `posthoc_parse_with_retry` — parse, and on failure feed a repair prompt up to
  `max_validation_retries` times.

Validation failures raise `OutputValidationError` (**fail-fast**), carrying the
raw text and the validator error. Retry is opt-in via the retry strategy.
`OutputFallback` controls behavior when the chosen strategy is unsupported.

## Consequences

- Portable structured output across heterogeneous providers.
- The application decides how to recover from validation failure.
- Four code paths to maintain.
- Fail-fast can surprise users expecting silent retries (documented; opt-in
  available).

## Alternatives considered

- **Single strategy** — rejected: no provider covers every case.
- **Always-retry default** — rejected (PRD §26 Q10): hides failures and burns
  tokens without the caller's consent.
