# tools

`tools` owns runtime-executed local tools and hosted-tool contracts: local
Python tools, provider-native hosted-tool specs, hosted-tool call/result
records, catalogs, isolated tool sessions, and client-side hosted-tool
execution.

## Belongs Here

- Local tool registration and provider-neutral schema export.
- Tool execution, timeout, concurrency, and context injection.
- Tool sessions for run-scoped tool registration.
- Tool catalog/search helpers.
- Provider-native hosted-tool specs under `hosted/specs.py`.
- Hosted-tool call/result contracts under `hosted/calls.py`.
- Runtime handlers for client-executed hosted tools.

## Does Not Belong Here

- MCP transport/client implementation.
- Workspace provider implementation details.

## Compatibility

`agent_runtime.hosted_tools` remains as a re-export shim for older imports.
New code should import specs from `agent_runtime.tools.hosted.specs`.
