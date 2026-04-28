# integrations

`integrations` contains optional third-party integration builders that compose
existing runtime primitives.

## Belongs Here

- Convenience constructors for external services.
- Integration-specific auth helpers when they produce runtime specs.
- Small adapters that return MCP specs, toolsets, or provider-neutral runtime
  configuration.
- Example/demo helpers that create temporary provider resources and return
  runtime-native specs or handles, such as OpenAI vector stores for `FileSearch`.

## Does Not Belong Here

- Core provider contracts.
- Runtime orchestration.
- Large provider SDK implementations that should live in adapter packages.

## Boundary Note

Integration modules should be optional and imported lazily from public package
exports when they require extra dependencies or credentials.
