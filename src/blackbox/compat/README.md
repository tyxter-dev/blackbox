---
status: active
owner: blackbox-src
since: 2026-06-27
adr:
  - docs/adr/0002-chat-messages-are-a-projection.md
  - docs/adr/0008-colon-routing-separator.md
prd: docs/prd/01-architecture.md
---

# compat

`compat` contains explicit migration and compatibility surfaces. These helpers
make older or external calling patterns usable without letting them define the
runtime internals.

## Belongs Here

- Chat-shaped input/output projections.
- Provider route compatibility helpers.
- Default provider registration helpers for simple examples or migration paths.

## Does Not Belong Here

- Core runtime state.
- Provider-native adapter logic.
- New primary API surfaces.

## Boundary Note

Compatibility helpers should project into canonical runtime contracts. They
should not become the source of truth for events, state, tools, or sessions.
