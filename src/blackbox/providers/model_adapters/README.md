# providers.model_adapters

`providers.model_adapters` contains `ModelProvider` implementations. Each
module maps one provider's model-turn API into the common runtime contract while
preserving provider-native state, event semantics, tools, controls, usage, and
raw payloads.

## Belongs Here

- Direct model-turn adapters.
- Provider-native request mapping.
- Provider-native streaming event mapping.
- Model-specific capability profiles and validation helpers.
- Deterministic/test model providers.

## Does Not Belong Here

- Agent-session providers.
- Runtime loop orchestration.
- Local tool execution.
- Workspace backend behavior.

## Compatibility

`blackbox.models` remains as a compatibility package for older imports.
