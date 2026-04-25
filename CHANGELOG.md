# Changelog

## Unreleased

### Slice 10 — `posthoc_parse_with_retry` + raw redaction sink

Closes two ⏳ rows in `tests/VALIDATION.md` (1.9 and 5.8). Surgical: no
new top-level surface, only wiring.

Output retry (PRD §10.7, P1-R12):
- `AgentRuntime.run` now accepts `output_spec=OutputSpec(...)` in addition
  to the convenience `output_type=Class` shortcut. The two are mutually
  exclusive; passing both raises `ValueError`.
- When `output_spec.strategy == "posthoc_parse_with_retry"`, validation
  failures feed the bad text and the validator error back into the model
  via a repair prompt and re-run the loop, up to
  `max_validation_retries` extra attempts. Final attempt's
  `OutputValidationError` is raised on exhaustion.
- `AgentResult.metadata["validation_attempts"]` reports how many passes
  were needed (1 on success, N+1 on retry).

Redaction sink (PRD §17–18, supports VALIDATION 5.8):
- `RedactingEventSink` wraps any `EventSink` and rewrites
  `event.raw` payloads when they are tagged with a `RawEnvelope` whose
  `sensitivity ∈ {sensitive, secret}` or `storage_allowed=False`.
- Pass-through behavior preserved for bare SDK objects on `event.raw` —
  adapters opt-in to redaction by wrapping in `RawEnvelope`.
- Custom `RedactionPolicy` callable + custom marker supported.
- Re-exported from `agent_runtime.observability`.

Tests:
- `tests/runtime/test_runtime_retry.py` — 5 tests: retry succeeds,
  retry exhausted raises with raw_text preserved, default strategy does
  not retry, single-attempt success records 1 attempt, output_type +
  output_spec mutually exclusive.
- `tests/unit/test_event_sinks.py` — 9 tests: memory sink ordering,
  pass-through cases (None, non-envelope, internal envelope), redaction
  for sensitive/secret/storage-disallowed payloads, custom marker,
  custom policy, and the default policy contract.
- 95 → 109 passing offline; 2 skipped (network-gated). Ruff +
  mypy --strict clean.

### Slice 12 — M2 workspace primitives + policy hooks

First cut of the workspace layer (PRD §15, M2). Builds the data contracts
and a local-directory runtime that emits canonical workspace events and
honors the workspace-relevant `Policy` checkpoints.

- New data classes in `agent_runtime.workspaces`:
  - `WorkspaceRef` (stable handle for an opened workspace),
  - `WorkspaceMount` (workspace mounted into an agent's environment),
  - `CommandSpec` / `CommandResult`,
  - `PatchArtifact` (typed patch with `.to_artifact()` interop).
- New `WorkspaceRuntime` (`workspaces/runtime.py`) for `WorkspaceSpec.kind ==
  "local"`. Surface: `open`, `read_file`, `write_file`, `delete_file`,
  `apply_patch`, `run_command`, `snapshot`, `list_artifacts`, `drain_events`.
- Emits canonical events: `WORKSPACE_FILE_READ`, `WORKSPACE_FILE_CHANGED`,
  `WORKSPACE_COMMAND_STARTED`, `WORKSPACE_COMMAND_COMPLETED`,
  `WORKSPACE_PATCH_CREATED`, `ARTIFACT_CREATED`.
- Wires three M2 policy checkpoints: `before_workspace_write`,
  `before_command`, `before_artifact_export`. `deny` raises
  `WorkspaceError`; `require_approval` raises `ApprovalError` (the workspace
  layer has no approval channel yet — integration with the `AgentLoop`
  approval flow lands in a later slice).
- Path-escape protection: every relative path is resolved and verified to
  stay under the workspace root.
- File I/O runs on `asyncio.to_thread` so async callers don't block.
- Default `CommandExecutor` uses `asyncio.create_subprocess_shell` /
  `_exec`; an injected executor fully replaces it for tests and sandboxes.
- 16 unit tests in `tests/unit/test_workspace_runtime.py` cover open
  validation, read/write/delete events, policy deny + require_approval,
  path-escape, patch application + artifact lifecycle, command success +
  failure + denial, snapshot + artifact export gating, and event drain.
- 79 → 95 passing offline tests; ruff + mypy --strict clean.

Deferred to later slices: AgentLoop integration (workspace ops as a tool
backend), git/sandbox/cloud workspace kinds, real archive-backed snapshots,
approval-channel wiring at workspace checkpoints.

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
