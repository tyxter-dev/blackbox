# ADR-0005 — ProviderState is provider-native, not chat history

- **Status:** Accepted
- **Recorded:** 2026-06-24 (decision made during 2026-04/05 design)
- **Sources:** PRD §10.3; `AGENTS.md` → Architecture in five points #5. Relates to [ADR-0002](0002-chat-messages-are-a-projection.md).

## Context

Continuing a conversation or session across turns requires the provider's native
continuation handles — `previous_response_id`, conversation IDs, response-output
items, Anthropic tool/MCP state, Gemini grounding/file metadata — not a
reconstructed chat transcript. Rebuilding a transcript loses fidelity and breaks
provider features that key off native IDs.

## Decision

`ProviderState` preserves native continuation inside `tool_state` / `continuation`
(plus `conversation_id`, `previous_response_id`, `reasoning_state`, etc.). The
core runtime never reduces `ProviderState` to a chat-message transcript; adapters
own their continuation shape.

## Consequences

- Faithful, provider-native resumption and continuation.
- Each adapter keeps its own continuation representation.
- State shape varies per provider; persistence layers must handle opaque,
  provider-specific blobs (the JSONL/SQLite stores do).

## Alternatives considered

- **Transcript-based continuation** (replay a message list each turn) — rejected:
  lossy and incompatible with native response-chaining and server-side state.
