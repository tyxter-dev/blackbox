# Architecture Notes

## Why separate model, agent, and workspace surfaces?

`ModelProvider`, `AgentProvider`, and the workspace contracts are intentionally
separate because they represent different units of work.

- `ModelProvider` runs model turns.
- `AgentProvider` runs local or cloud-managed agent sessions.
- Workspace contracts describe where agents work and how governed workspace
  agents are packaged, permissioned, scheduled, shared, and audited.

Hosted/cloud agents are not model calls with more tools. Likewise, a workspace
agent package is not only an agent session: it carries connector auth modes,
tool permissions, schedule metadata, publication metadata, skills, and memory
policy that downstream products can persist or expose in an agent directory.

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
6. First-class `runtime.workspaces` facade with local, git, sandbox, Docker,
   and cloud workspace provider contracts plus the compatibility workspace
   tool bridge.
7. Local MCP connector for registered tools with namespaced refs and policy gates.
8. Workspace agent package contracts, permissions, schedules, serialization,
   registry protocol, and thin runtime bridge.
9. Workflow trace context on every event, OpenTelemetry export, replay/diff
   helpers, and evaluator hooks.

Remaining architectural targets:

1. Vertex Agent Engine execution.
2. Production presets for external trace, metrics, artifact, and evaluation backends.
