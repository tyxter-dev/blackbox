# Changelog

## 0.1.0 — MVP

The first release that satisfies PRD §27 *Definition of done for v0.1*.

### Core protocols and models
- `ModelProvider` and `AgentProvider` runtime-checkable protocols.
- `ProviderRegistry` with `provider/resource` reference parsing and aliases.
- `AgentEvent` + canonical `EventTypes` covering run/session, model, tools,
  tool-search, MCP, cloud-agent, workspace, approval, artifact, and eval domains.
- `RunItem` + `ItemTypes`, `ProviderState`, `RunState`, `AgentSession`,
  `AgentRef`, `SessionRef`, `Artifact`, `ArtifactRef`, `ApprovalRequest`,
  `ApprovalDecision`, `ModelCapabilities`, `AgentCapabilities`.
- Full error hierarchy per PRD §19 (`AgentRuntimeError` →
  `ConfigurationError`, `CapabilityError`, `ProviderExecutionError`,
  `ApprovalError`, `SessionError`, `ArtifactError`, `WorkspaceError`,
  `MCPError`, …).

### Runtime
- `AgentRuntime` exposes separate `runtime.models` and `runtime.agents`
  facades (PRD §8.3).
- `ModelRuntime.run/.stream` accept and emit `ProviderState` for native
  continuation across turns.
- `AgentRuntimeFacade` covers create/start/stream/run/send_message/approve/
  cancel/list_artifacts.

### Tooling
- `ToolRegistry` + `ToolRuntime` with parameter-name context injection that
  is never exposed to the model schema, blocking-tool offload, timeouts,
  concurrency caps, and string/dict result coercion.
- `ToolCatalog` with simple relevance-scored search.

### Local agent
- `LocalAgentProvider` drives a multi-turn loop: streams model events,
  dispatches `tool.call.requested` through `ToolRuntime`, feeds
  `FUNCTION_RESULT` items back as the next turn's input, and surfaces
  `session.failed` if `max_iterations` is exceeded.
- Approval flow: optional `approval_policy` causes the session to enter
  `waiting`, emit `approval.requested`, and resume after
  `runtime.agents.approve(...)`.
- Cancellation honored between turns; emits `session.cancelled`.

### Providers
- `EchoModelProvider` — dependency-free test provider with a
  `ProviderState` turn counter.
- `OpenAIResponsesProvider` — provider-native Responses adapter: typed
  events for stable items (message, reasoning, function_call,
  tool_search_*, mcp_*) and a `MODEL_ITEM_CREATED` fallback that preserves
  raw payloads for evolving hosted tools. Round-trips
  `previous_response_id`. Lazy-imports the `openai` package.
- Stub providers preserved for Anthropic Messages, Gemini, OpenAI cloud
  agents, Anthropic Managed Agents, and Google Agent Platform.

### Tests, lint, types
- 48 unit tests covering registry, capabilities, data models, errors,
  provider-state round-trip, echo provider, OpenAI Responses adapter,
  local-agent tool/approval/cancel/max-iteration paths, and tool runtime
  edge cases.
- `integration_openai` (and stubs for anthropic/gemini/google_platform/
  cloud_agent/mcp) pytest markers; OpenAI smoke test gated by
  `OPENAI_API_KEY`.
- `ruff check` and `mypy --strict` clean.

### Examples
- `examples/echo_run.py`, `examples/local_agent_with_tool.py`,
  `examples/openai_responses_run.py`.
