# Changelog

## Unreleased

### Slice 9 — Anthropic Messages-native model provider (P1-R2)

Second real `ModelProvider`, mirroring the M1 OpenAI Responses pattern.

- `AnthropicMessagesProvider.stream_turn` maps native streaming events to
  canonical `AgentEvent`s while preserving raw provider payloads:
  - `text` blocks → `MODEL_ITEM_CREATED` plus `MODEL_TEXT_DELTA` per
    `text_delta`, then `MODEL_ITEM_COMPLETED`.
  - `thinking` blocks → reasoning items with `MODEL_REASONING_DELTA`.
  - `tool_use` blocks accumulate `input_json_delta` chunks and emit a
    single `TOOL_CALL_REQUESTED` (with parsed `arguments`) at
    `content_block_stop`.
  - `server_tool_use`, `web_search_tool_result`, and other evolving hosted
    blocks fall back to `MODEL_ITEM_CREATED` with the original block type in
    `data["item_type"]` and the raw block preserved on `event.raw`.
- `ProviderState.native_history` carries the full Anthropic-shaped
  `messages` array for multi-turn continuation. `FUNCTION_RESULT` `RunItem`s
  fed back into the loop are converted into a `tool_result` user message
  whose `tool_use_id` round-trips the original `call_id`.
- Provider-neutral tool descriptors (OpenAI shape) are converted to
  Anthropic's `{name, description, input_schema}` form transparently.
- Capabilities advertise `supports_parallel_tool_calls=True` to match
  Claude's actual behavior.
- `[anthropic]` extra installs `anthropic>=0.40,<1`; client is lazy-imported.

### Tests
- 8 golden tests in `tests/golden/anthropic/test_messages_event_mapping.py`
  drive a `FakeAnthropicClient` (mirrors the `FakeOpenAIClient` shape) over
  text-only, tool-use with parsed input, thinking deltas, hosted-block
  fallback, multi-turn `tool_result` round-trip, tool schema conversion,
  missing-credentials, and SDK-failure paths.
- Network-gated `tests/integration/anthropic/test_messages_smoke.py`
  (`integration_anthropic` marker, skipped without `ANTHROPIC_API_KEY`).
- 71 → 79 passing offline tests; 1 skipped (network-gated). Ruff +
  mypy --strict clean.

## 0.2.0 — Blackbox `runtime.run(...)` and `AgentResult[T]`

Closes the gap between PRD §6 UC0 and the v0.1 scaffold. The high-level
product promise from `llm_factory_toolkit` v1 is now restored on top of the
provider-native primitives.

### High-level API
- `AgentRuntime.run(input=..., tools=..., output_type=..., tool_execution_context=...,
  approval_policy=..., max_iterations=..., mock_tools=...)` returns
  `AgentResult[T]`. Owns the complete tool/model loop end-to-end (PRD P0-R11).
- `AgentRuntime.stream(...)` yields the same canonical events for callers
  that want to observe the loop.
- `AgentResult[T]` carries `output`, `text`, `events`, `items`, `artifacts`,
  `payloads`, `provider_state`, `metadata` (PRD §10.7, P0-R12).
- `runtime.tools` facade with `register`, `get`, `all_tools`,
  `to_provider_tools`, `call(..., mock=...)` (P0-R13).

### Loop extraction
- New `agent_runtime.loop.AgentLoop` owns the iterative control flow per
  PRD §8.4. Both `LocalAgentProvider` (session-shaped API) and
  `AgentRuntime.run` (blackbox API) share the same loop body.

### Tooling
- `ToolRuntime.call(..., mock=True)` short-circuits real execution and
  returns a canonical mock result.
- `ToolResult.payload` is now plumbed through tool events and aggregated
  into `AgentResult.payloads` (deferred-payload pattern from llm_toolkit).
- `tool_execution_context={"user_id": ...}` injects context per-call without
  exposing the parameter in the schema sent to the model.

### Output validation
- Pydantic `BaseModel` and `@dataclass` `output_type`s validated via
  `model_validate_json` / `**json.loads`. Failures raise the new
  `OutputValidationError` (fail-fast, carries `raw_text` and `cause`).

### Tests
- 10 new offline tests in `test_runtime_blackbox.py` mirroring the
  `llm_factory_toolkit` v1 contract: text-only, Pydantic output, single tool,
  three parallel tools, context-injection privacy, deferred-payload
  collection, mock-tool short-circuit, validation error, stream parity.
- 48 → 58 passing tests; 1 skipped (network-gated). Ruff + mypy --strict clean.

### Examples
- `examples/run_with_typed_output.py` runs the full blackbox loop offline
  with an inline scripted model and a Pydantic `TicketDecision` output type.

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
