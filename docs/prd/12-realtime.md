# 12 — Feature: Realtime (voice/audio sessions)

**Facade:** `runtime.realtime` · **Package:** `src/blackbox/realtime/` ·
**Protocol:** `RealtimeProvider`

## Summary

`RealtimeRuntime` manages low-latency audio/voice sessions. The protocol surface
is deliberately **separate** from `ModelProvider` because realtime transports
have distinct lifecycle semantics: mutable session config, server-pushed turn
detection, audio deltas, interruptions, and transcripts. A realtime session is
not a model turn and not a standard agent session.

## Why a separate protocol

A model turn is request/response with streamed deltas; a realtime session is a
persistent bidirectional connection whose configuration can change mid-session
and where the *server* signals turn boundaries and interruptions. Forcing this
into `ModelProvider.stream_turn` would distort both. Hence
`RealtimeProvider` / `RealtimeRuntime` / `ManagedRealtimeSession`.

## Capabilities

- WebSocket (and provider-native) transport with mutable session config.
- Audio in / audio out as event deltas.
- Server-side turn detection and interruption handling.
- Live transcripts.
- Connect / mutate / close lifecycle via `runtime.realtime`.

## Providers

- **OpenAI Realtime**
- **Gemini Live**
- **`FakeRealtimeProvider`** — for offline tests.

## Events

~50 of the ~148 event-type constants belong to the realtime family
(`realtime.turn.completed` and relatives — audio deltas, turn detection,
interruptions, transcripts).

## Requirements

Realtime is not enumerated in the original P0/P1/P2 tables (it postdates them).
It is governed by the same architectural constraints as every other surface and
configured by the `realtime_voice` profile (see [13](13-configuration.md)).

## Hard constraints

- Realtime stays a **separate protocol family** from `ModelProvider` — distinct
  lifecycle semantics.
- Same escape-hatch rule: preserve raw provider payloads.

## Open question (Horizon 3)

Decide whether realtime stays first-class or is demoted to "experimental" in the
docs — it widens the adapter maintenance treadmill and should earn its keep.

## Status & references

OpenAI Realtime, Gemini Live, and `FakeRealtimeProvider` shipped; `realtime_voice`
profile available. Tests: `tests/unit/realtime/`. `ROADMAP.md` Horizon 3
(realtime hardening).

→ Next: [13 — Configuration](13-configuration.md)
