# Architecture Notes

## Why two provider protocols?

`ModelProvider` and `AgentProvider` are intentionally separate because hosted/cloud agents are not model calls with more tools. They have lifecycle, artifacts, permissions, state, sessions, approvals, and sometimes deployment/evaluation operations.

## Provider compatibility without LiteLLM

This scaffold takes the routing idea but not the dependency or format normalization:

- Providers register themselves by key.
- Provider refs parse strings like `openai/gpt-5` or `google-agent/my-agent`.
- Capabilities are explicit.
- Provider-native state is stored in `ProviderState` and adapter-specific metadata.
- Events and items are normalized; native payloads are preserved.

## Current milestone posture

The local model/tool loop is implemented and covered by the validation
catalog. `AgentRuntime.run(...)` and `AgentRuntime.stream(...)` drive a shared
`AgentLoop`, dispatch local tools through `ToolRuntime`, collect
`AgentResult[T]`, preserve provider state, and emit correlated events.

Implemented provider/runtime slices:

1. `EchoModelProvider` and deterministic scripted-model fixtures for local tests.
2. OpenAI Responses-native event mapping and continuation state.
3. Anthropic Messages-native event mapping and continuation state.
4. Gemini GenerateContent-native event mapping and continuation state.
5. JSONL event store and SQLite run store adapters.
6. Local workspace runtime and workspace tools registered through the high-level loop.
7. Local MCP connector for registered tools with namespaced refs and policy gates.

Remaining architectural targets:

1. Cloud/coding-agent providers with real session streaming.
2. MCP stdio, HTTP/SSE, and streamable HTTP transport management.
3. Provider-native remote MCP configuration passthrough.
4. Workspace approval-channel integration and non-local workspace kinds.
5. OpenTelemetry-style trace export and richer replay/debug tooling.
