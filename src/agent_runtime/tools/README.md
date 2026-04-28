# tools

`tools` owns runtime-executed tool behavior: local Python tools, catalogs,
isolated tool sessions, and client-side execution for hosted-tool calls.

## Belongs Here

- Local tool registration and provider-neutral schema export.
- Tool execution, timeout, concurrency, and context injection.
- Tool sessions for run-scoped tool registration.
- Tool catalog/search helpers.
- Runtime handlers for client-executed hosted tools.

## Does Not Belong Here

- Provider-hosted tool request specs that are only sent to model APIs.
- MCP transport/client implementation.
- Workspace provider implementation details.

## Transitional Boundary

Typed hosted-tool specs currently live in `agent_runtime.hosted_tools`. They are
part of the tool domain and should move under `tools/hosted/` once compatibility
re-exports are added.
