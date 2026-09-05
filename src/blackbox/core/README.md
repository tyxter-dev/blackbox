---
status: active
owner: blackbox-src
since: 2026-06-27
adr:
  - docs/adr/0002-chat-messages-are-a-projection.md
  - docs/adr/0004-preserve-raw-provider-payloads.md
  - docs/adr/0005-provider-native-provider-state.md
  - docs/adr/0006-zero-dependency-core-pydantic-optional.md
  - docs/adr/0010-workspaces-and-persistence-in-core.md
prd: docs/prd/01-architecture.md
---

# core

`core` contains stable runtime primitives that are shared by multiple domains.
These modules should be small, provider-neutral, and safe for other packages to
import without causing adapter or runtime orchestration side effects.

## Belongs Here

- Canonical events, run items, state, sessions, artifacts, approvals, and errors.
- Provider-neutral result and content/media contracts.
- Policy and persistence protocols that are used across runtime surfaces.
- Capability records that describe what providers can support.

## Does Not Belong Here

- Provider adapter implementation logic.
- High-level orchestration or agent-loop control flow.
- Domain-specific prompt content or business workflow policy.
- Workspace backend implementation details.

## Transitional Modules

- `prompts.py` and `run_plan.py` are compatibility re-export shims. New code
  should import from `blackbox.planning`.
- `cache.py` and `stores.py` are shared infrastructure. They may remain here or
  move to an infrastructure package if they continue to grow.

`tool_permissions.py` holds immutable package grants, context-local boundary
management, and canonical authorization request helpers shared by runtime domains.
