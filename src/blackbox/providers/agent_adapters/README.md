---
status: active
owner: blackbox-src
since: 2026-06-27
adr:
  - docs/adr/0003-separate-model-and-agent-protocols.md
  - docs/adr/0004-preserve-raw-provider-payloads.md
  - docs/adr/0005-provider-native-provider-state.md
prd: docs/prd/04-agent-providers.md
---

# providers.agent_adapters

`providers.agent_adapters` contains `AgentProvider` implementations. These
modules supervise agent sessions rather than individual model turns: they map
create/start/stream/follow-up/approval/cancel/artifact operations onto a local
loop, SDK, or cloud agent surface.

## Belongs Here

- Local model-backed agent providers.
- Cloud or SDK-backed coding-agent providers.
- Agent-session lifecycle mapping.
- Provider-native session, approval, artifact, and continuation state.
- Agent response projection events such as multi-message conversational replies.

## Does Not Belong Here

- Direct model-turn adapters.
- Local Python tool registry/runtime code.
- Workspace provider implementations.
- Workspace-agent package metadata and scheduling contracts.

## Compatibility

`blackbox.agents` remains as a compatibility package for older imports.
