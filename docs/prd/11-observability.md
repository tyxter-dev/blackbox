# 11 — Feature: Observability

**Package:** `src/blackbox/observability/` · **Entry point:**
`ObservabilityPreset` · `AgentRuntime(observability=...)`

## Summary

Every run and session is an event stream first; observability consumes those
records. Runtime modules *emit* canonical events and metadata; observability
modules *consume* them — they don't own behavior that produces them. The
`EventStore` (in-memory by default) is the durable backbone; sinks, traces, and
metrics layer on top.

## Event sinks

Event sinks receive: all canonical events; provider raw events where allowed;
timing metadata; usage and cost metadata; session status transitions; tool and
workspace operations; artifact-creation events; and approval decisions.

Sink kinds: in-memory collection, callback sink, JSONL sink, and an
OpenTelemetry-style trace adapter.

## Production preset

`ObservabilityPreset.production(...)` wires runtime event logging, trace export,
and metric export from a single `AgentRuntime(observability=...)` argument. It:

- **redacts** raw provider payloads at the trace layer (rather than stripping
  them at the source — raw stays in the event store),
- attaches provider request/session IDs to spans,
- emits standardized operation names: `agent.run`, `model.turn`, `tool.call`,
  `mcp.list_tools`, `workspace.command`, `approval.wait`, `cache.lookup`,
  `artifact.write`.

## Replay, diff & evals

Replay/diff helpers reconstruct a run from stored events (debug stored runs;
diff two runs). The evals surface can run and stream evaluation events
(`eval.started` / `eval.completed`) for providers/adapters that expose it.

## Persistence

Store protocols with multiple backends: `EventStore`, `RunStore`,
`SessionStore`, `ProviderCacheStore` — each has in-memory, JSONL, and SQLite
implementations. Defaults are in-memory and wired into `AgentRuntime`.

## Accounting

Cost and usage accounting aggregates usage across model turns, tools, and cloud
sessions (`core/accounting`, bundled pricing catalog — a snapshot needing
periodic refresh).

## Requirements

| ID | Priority | Requirement |
|---|---|---|
| P0-R14 | P0 | Event correlation via `run_id` + monotonic `sequence`; `EventStore.list_events(run_id, after_sequence=...)` returns the ordered tail. |
| P0-R15 | P0 | `EventStore` + `RunStore` Protocols with in-memory defaults; JSONL/SQLite impls included. |
| P1-R10 | P1 | Event sinks can receive all runtime events. |
| P2-R6 | P2 | Persist sessions, events, artifacts, and provider state to external stores. |
| P2-R7 | P2 | Cost and usage accounting aggregated across turns, tools, and cloud sessions. |

## Hard constraints

- **Raw provider payloads are preserved at the source**; production presets
  redact at the *trace* layer, never by stripping `raw` from events.
- Observability consumes events; it does not produce runtime behavior.

## Status & references

JSONL `EventStore` + SQLite `RunStore` shipped; OpenTelemetry exporter and
replay/diff tooling shipped; production preset shipped. Realtime hardening note:
decide whether realtime observability stays first-class or is demoted to
experimental (Horizon 3). Tests: `tests/unit/observability/`. PRD §18, §22 M5,
§12 (P0-R14/R15, P1-R10, P2-R6/R7).

→ Next: [12 — Realtime](12-realtime.md)
