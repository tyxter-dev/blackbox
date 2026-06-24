# ADR-0006 — Zero-dependency core; dataclasses in core, Pydantic optional

- **Status:** Accepted
- **Recorded:** 2026-06-24 (decision made during 2026-04/05 design)
- **Sources:** PRD §26 Q4; `ROADMAP.md` decision log #4; `AGENTS.md` → Things to ask before doing

## Context

Structured-output validation benefits from Pydantic, but a hard Pydantic
dependency would burden every consumer of the runtime — including those who only
make plain model calls. The core contracts (events, items, state, results) need
to be light and dependency-free.

## Decision

The runtime core has **zero hard dependencies**. Value types are dataclasses
(`slots=True`; frozen for ID/ref/config objects). Pydantic is an optional
`validate` extra and is imported lazily inside `_validate_output`. The runtime
works without it unless the caller passes a Pydantic `output_type`. Output types
may be Pydantic models, dataclasses, or raw JSON Schema.

## Consequences

- Lightweight core installable anywhere; optional extras (`openai`, `anthropic`,
  `google`, `mcp`, `validate`) layer on top. `dev` extras include Pydantic so
  examples/tests run out of the box.
- Two validator paths (dataclass / Pydantic) to support.
- Dataclass schemas are less ergonomic than Pydantic for complex validation.

## Alternatives considered

- **Pydantic everywhere (hard dep)** — rejected: burdens all consumers.
- **No Pydantic support** — rejected: Pydantic is the validator of choice for
  many users and was a proven win in v1.
