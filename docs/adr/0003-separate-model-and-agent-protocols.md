# ADR-0003 — Separate ModelProvider and AgentProvider protocols

- **Status:** Accepted
- **Recorded:** 2026-06-24 (decision made during 2026-04/05 design)
- **Sources:** PRD §4.2, §4.6, §9; `AGENTS.md` → Architecture in five points #1

## Context

A cloud/managed coding agent is not a model turn with more tools. A session has
its own lifecycle, event stream, approvals, cancellation, follow-up invocations,
artifacts, provider-native IDs, and sometimes a provider-managed workspace.
Forcing that through a `generate()`/model API — or modeling a cloud agent as a
callable tool — distorts both.

## Decision

Two protocols, kept separate:

- `ModelProvider.stream_turn(...)` runs **one model turn**.
- `AgentProvider` runs **sessions** (`create_agent` / `start_session` /
  `stream_events` / `send_message` / `approve` / `cancel` / `list_artifacts`).

Cloud agents are first-class sessions, never ordinary tools. `AgentLoop` sits
between model turns and tool execution and is shared by `LocalAgentProvider` and
`AgentRuntime.run`.

## Consequences

- Cloud and local agents are modeled faithfully with native lifecycle/state.
- Two provider surfaces to maintain and document.
- Callers must know whether they want a model turn or a session (the high-level
  `runtime.run` hides this for the common case).

## Alternatives considered

- **Single provider protocol** for both — rejected: collapses session semantics.
- **Cloud-agent-as-tool** — rejected (PRD §4.6): hides lifecycle, approvals, and
  artifacts behind a fake function call.
