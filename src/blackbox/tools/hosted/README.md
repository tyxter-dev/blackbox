# tools.hosted

`tools.hosted` owns hosted-tool contracts.

## File Map

- `specs.py`: provider-native hosted-tool specs such as `WebSearch`,
  `FileSearch`, `RemoteMCP`, `ToolSearch`, and conversion helpers for provider
  request payloads.
- `calls.py`: runtime-side hosted-tool call, context, output, and handler
  contracts used when a provider asks the client/runtime to execute a hosted
  tool.

## Compatibility

`blackbox.hosted_tools` remains as a re-export shim for older imports.
`blackbox.tools.hosted` re-exports the call/result contracts and the
hosted-tool specs for convenience.
