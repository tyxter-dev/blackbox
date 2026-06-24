# ADR-0010 — Workspaces and persistence interfaces are core in v0.1

- **Status:** Accepted
- **Recorded:** 2026-06-24 (decision made during 2026-04/05 design)
- **Sources:** PRD §26 Q3/Q5; `ROADMAP.md` decision log #3/#5

## Context

Two early open questions: ship workspaces in core for v0.1 or defer to v0.2; and
introduce persistence interfaces immediately or later. Coding agents — the
primary use case — need a place to work and need resumable, observable state from
day one.

## Decision

Both ship in core for v0.1:

- `WorkspaceProvider` is a first-class facade with local, git, sandbox, Docker,
  and cloud backends.
- Persistence is defined as protocols — `EventStore`, `RunStore`, `SessionStore`,
  `ProviderCacheStore` — each with in-memory defaults plus JSONL and SQLite
  implementations.

## Consequences

- The coding-agent use case (files, commands, patches, artifacts) is supported
  immediately; resumption and observability are designed in early
  (`run_id`/`sequence`, store protocols).
- Larger core surface to maintain.
- Workspace backends span local to opaque cloud references, so the workspace
  contract must not assume local filesystem access.

## Alternatives considered

- **Defer workspaces/persistence to v0.2** — rejected: would push the primary
  use case and resumability past the first release and force a later redesign of
  state and event correlation.
