# output

`output` owns structured-output schema conversion and validation helpers.

## Belongs Here

- Conversion from Python/Pydantic/dataclass output specs to JSON Schema.
- Validation helpers used by provider-native, finalizer-tool, and posthoc parse
  output strategies.
- Small utilities shared by model adapters and the runtime loop.

## Does Not Belong Here

- Provider adapter request mapping except for using the generated schema.
- Prompt text for output behavior.
- Final agent-result collection.

## Boundary Note

The strategy selection contract lives in `core.results.OutputSpec` today.
Output-strategy-specific prompt fragments belong with `agent_runtime.planning`,
not in this package.
