---
status: active
owner: blackbox-src
since: 2026-06-27
adr:
  - docs/adr/0006-zero-dependency-core-pydantic-optional.md
  - docs/adr/0007-four-output-strategies-fail-fast.md
prd: docs/prd/10-structured-output.md
---

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
Output-strategy-specific prompt fragments belong with `blackbox.planning`,
not in this package.
