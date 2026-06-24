# ADR-0008 — `:` is the canonical provider:model routing separator

- **Status:** Accepted
- **Recorded:** 2026-06-24 (decision made during 2026-04/05 design)
- **Sources:** `AGENTS.md` → Conventions → Routing format; PRD §7.1

## Context

The high-level API routes by a `provider`+`model` string. The earlier `/`
separator collides with namespaced agent paths such as
`vertex-agent-engine/projects/foo/agent`, making `provider/model` ambiguous.

## Decision

`provider:model` is the canonical separator at the high level
(`model="openai:gpt-5.5"`). `/` is still accepted for backward compatibility, and
new examples must use `:`.

## Consequences

- No ambiguity between `provider:model` and namespaced agent paths.
- Two accepted input forms (canonical `:`, legacy `/`).
- Docs and examples standardize on `:`.

## Alternatives considered

- **`/` only** — rejected: collides with agent path namespaces.
- **Drop `/` entirely** — rejected: would break existing callers; kept as
  backward-compatible input.
