# models

`models` contains `ModelProvider` adapter implementations. Each module maps one
provider API into the common model-turn contract while preserving provider-native
state, events, controls, hosted tools, usage, and raw payloads.

## Belongs Here

- Direct model-turn adapters.
- Provider-native request mapping.
- Provider-native streaming event mapping.
- Model-specific capability profiles and feature gates.
- Test or fake model providers used by runtime tests.

## Does Not Belong Here

- Multi-turn agent-loop orchestration.
- Cloud-managed agent session providers.
- Local tool execution.
- Workspace filesystem or sandbox behavior.

## Naming Direction

This package may eventually become `providers/model_adapters/`. Until then,
`models/` means "model-provider adapters", not domain models or data schemas.
