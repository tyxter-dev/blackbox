# agents

`agents` contains `AgentProvider` adapter implementations. These providers
supervise agent sessions, which are different from individual model turns.

## Belongs Here

- Local model-backed agent provider adapters.
- Cloud or SDK-backed coding-agent adapters.
- Session lifecycle mapping: create, start, stream, follow up, approve, cancel.
- Provider-native session and artifact handling.

## Does Not Belong Here

- Direct model-turn adapters.
- Local Python tool registry/runtime code.
- Workspace provider implementations.
- Workspace-agent package metadata and scheduling contracts.

## Naming Direction

This package may eventually become `providers/agent_adapters/`. Until then,
`agents/` means "agent-provider adapters", not business agents or prompt packs.
