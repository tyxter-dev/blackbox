# ADR-0002 — Chat messages are a compatibility projection, not canonical state

- **Status:** Accepted
- **Recorded:** 2026-06-24 (decision made during 2026-04/05 design)
- **Sources:** PRD §4.5, §24; `AGENTS.md` → Hard constraints. Relates to [ADR-0005](0005-provider-native-provider-state.md).

## Context

Most LLM libraries treat an alternating user/assistant message list as the source
of truth. Agent supervision needs events, durable items, provider-native
continuation state, and artifacts — all of which a flat message list represents
lossily or not at all.

## Decision

The runtime's canonical state is the event stream + `RunItem`s + `ProviderState`.
Chat messages exist only as an explicit, clearly-labeled compatibility export
(`runtime.chat`). No internal code path may treat alternating user/assistant
messages as the source of truth.

## Consequences

- Provider-native state and the full event log are preserved losslessly.
- The events log is the truth; text is one projection, structured `output` is
  another.
- Callers who want a chat shape use the export and accept it may be lossy.
- More concepts to learn than "a list of messages."

## Alternatives considered

- **Chat-message core** (the common approach) — rejected: lossy for events,
  items, provider state, and artifacts; would recreate the v1 limitation the
  product set out to move past.
