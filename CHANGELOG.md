# Changelog

## Unreleased

### Slice 22 — Provider-cost and billable pricing catalogs

Blackbox now separates raw API cost estimates from application billable price.

- Adds provider-cost and billable pricing paths to `ModelCatalog`, with
  `MarkupPolicy` for common markup/minimum/rounding cases and user billable
  catalogs for custom resale rates.
- Emits `provider_cost`, optional `billable`, legacy `cost`, and grouped
  `accounting` metadata from high-level runs, direct model turns, and
  agent-session result collection.
- Seeds `AgentRuntime()` with a bundled provider-cost catalog for common
  OpenAI, Anthropic, Gemini, and xAI text models, with opt-out via
  `pricing=None` or `pricing="empty"`.
- Keeps user-registered provider pricing ahead of bundled rates and includes
  source/catalog provenance on bundled estimates.
- Leaves batch discounts, long-context premiums, hosted-tool charges, storage
  charges, and regional pricing as explicit future catalog dimensions.

### Slice 21 — MCP server authoring helpers

Blackbox now includes a small first-party SDK for authoring local stdio MCP
servers.

- Adds `MCPServer`, `MCPServerTool`, `MCPServerResult`, and `MCPToolError` for
  decorator-based MCP tool registration.
- Supports Pydantic v2 input models for JSON-schema exposure and argument
  validation without making Pydantic a hard runtime dependency.
- Normalizes handler returns into MCP text/structured-content payloads and
  converts business failures into `isError` tool results.
- Covers JSON-RPC initialize/list/call handling and a real `MCPConnector`
  stdio discovery/call path.

### Packaging — Blackbox rename and MIT license

- Renamed the public distribution and import package from `agent-runtime-core`
  / `agent_runtime` to `blackbox`.
- Added repository metadata for `tyxter-dev/blackbox`.
- Added the MIT license file and packaging metadata.

### Slice 20 — P0 hot-path performance gates

The benchmark watchlist is now backed by strict CI enforcement and concrete
hot-path optimizations for repeated runtime flows.

- Adds strict benchmark budgets for core P0 scenarios plus per-scenario p50/p95
  trend reporting from JSON benchmark output.
- Caches generated tool/output schemas and provider capability/profile
  validation results while preserving mutation isolation for callers.
- Batches event-store writes during high-volume streams and routes internal
  stream queues through bounded backpressure helpers.
- Avoids retaining large raw payloads when storage is not allowed and lazily
  materializes large workspace artifacts until export.
- Reuses identity-aware MCP discovery caches from runtime-managed local
  `MCPToolset` instances across runs.

### Slice 19 — MCP production compatibility hardening

MCP is now represented as a formal production compatibility boundary rather
than only a local tool convenience.

- Adds an MCP protocol compatibility matrix and routes client negotiation
  through the declared supported versions.
- Adds OAuth bearer token refresh support for remote HTTP MCP servers and a
  typed `MCPAuthenticationError` that carries safe retry/challenge metadata.
- Adds `MCPTrustPolicyPresets.local_only(...)`,
  `.enterprise_remote(...)`, and `.provider_native_allowed(...)` for common
  production trust postures.
- Expands MCP policy and approval metadata with server, tool, ref, scopes,
  risks, trust level, route mode, and trust fingerprint.
- Isolates MCP tool-list caches by auth identity and credential fingerprints,
  and invalidates cached discovery when a server emits a tools/listChanged
  notification.
- Redacts MCP call failure events by default, enforces per-tool output limits
  over server defaults, and adds transport timeout cancellation coverage.
- Normalizes provider-native RemoteMCP metadata so OpenAI/Anthropic MCP items
  expose comparable server/tool/ref/route fields to local MCP dispatch.
- Adds unit/golden coverage for protocol negotiation, auth refresh, cache
  invalidation, trust presets, redaction, output limits, and metadata parity.

### Slice 18 — Provider-native tool and structured-output hardening

Provider capability profiles now distinguish model-version support for
provider-native structured output, tool combinations, and compaction instead of
advertising one broad provider-wide surface.

- Adds typed Anthropic `TextEditor` and `Memory` hosted-tool specs while
  preserving `HostedToolRaw` as the explicit future-provider escape hatch.
- Maps Anthropic provider-native structured output through
  `output_config.format` for supported Claude model versions, and maps
  supported Claude 4.6 compaction controls to `context_management.edits`.
- Gates Gemini structured output plus built-in/function tools to Gemini 3
  model profiles, raising `UnsupportedFeatureError` before SDK dispatch for
  unsupported model/tool combinations.
- Preserves OpenAI output item IDs, hosted/MCP IDs, function call IDs, source
  references, file handles, Anthropic tool/MCP state, and Gemini grounding/file
  metadata in `ProviderState.tool_state`.
- Adds contract/runtime/golden tests proving unsupported requests fail before
  provider calls and continuation state retains native IDs.

### Slice 17 — MCP integration hardening and toolset routing

MCP is now a hardened external tool/server boundary with explicit local versus
provider-native routing.

- Extends `MCPServerSpec` with protocol, auth, filter, timeout, output-limit,
  approval-mode, remote-trust, and redaction fields.
- Adds lifecycle-aware `MCPClient` initialization with protocol negotiation and
  `notifications/initialized`.
- Upgrades stdio and streamable HTTP transports with JSON-RPC request IDs,
  request timeouts, notifications, session/protocol metadata, auth headers, and
  secret-safe error behavior.
- Adds MCP tool discovery cache keys that account for server identity, session,
  protocol, auth shape, and tool filters.
- Normalizes MCP call results into model-visible content plus preserved MCP
  payload metadata for runtime tool calls.
- Adds `MCPToolset` and `runtime.run(..., toolsets=[...])` routing for local
  MCP dispatch or provider-native `RemoteMCP`.
- Extends OpenAI `RemoteMCP` mapping with `connector_id` and filter-object
  `allowed_tools` while preserving Anthropic `mcp_servers` plus `mcp_toolset`.

### Slice 16 — First-class WorkspaceProvider routing

Workspace execution is now a provider layer instead of only a registered-tool
workaround for coding-agent flows.

- Adds `runtime.workspaces` as a provider registry/facade with built-in local,
  git, and opaque cloud providers plus support for registered sandbox and
  Docker workspace providers.
- Extends `WorkspaceSpec.git(url=..., ref=...)`, `WorkspaceSpec.docker(...)`,
  and `WorkspaceSpec.cloud(...)` constructors.
- Routes `runtime.agents.run(..., workspace=...)` through `WorkspaceProvider`
  resolution so `TaskSpec.workspace` carries a `WorkspaceRef` into provider
  sessions.
- Normalizes provider-native workspace file, command, patch, test, snapshot,
  approval, and artifact events with workspace metadata.
- Keeps `runtime.tools.register_workspace(...)` as a compatibility bridge for
  high-level model loops.

### Slice 15 — Managed-agent result collector

`runtime.agents.run(...)` now starts a provider-managed session and returns an
`AgentSessionResult[T]` instead of forcing callers to consume the event stream
manually.

- Adds `AgentSessionResult[T]` with typed `output`, final `text`, strict
  `status`, collected events, artifacts, `session_ref`, `provider_state`,
  `usage`, `trace`, and metadata.
- Extends agent session creation with direct `workspace`, input artifact,
  metadata, and provider-extra arguments so the collector supports coding-agent
  handoff in one call.
- Stores stamped agent-session events in `AgentRuntime.event_store` and writes
  compact session/provider cursor state to `RunStore`.
- Keeps `runtime.agents.stream(...)` as the lower-level supervision API for
  applications that need live event handling.

### Slice 14 — Workflow tracing, replay, OpenTelemetry, and eval hooks

Observability now covers full workflow-level traces instead of only compact
model-turn metadata.

- Adds trace context fields to every `AgentEvent`: `trace_id`, `span_id`,
  `parent_span_id`, `span_kind`, plus provider-native trace/span/request
  correlation fields.
- Stamps model, agent-session, and high-level agent-loop streams with trace
  context while preserving monotonic run-local event sequences.
- Expands `observability.traces` with workflow-rooted span reconstruction for
  model calls, tool calls, hosted tools, MCP, workspace actions, approvals,
  artifacts, retries, handoffs, guardrails, and eval events.
- Adds an optional `otel` extra and `OpenTelemetryTraceExporter` for exporting
  reconstructed traces through an application-configured OpenTelemetry SDK.
- Adds replay/diff helpers and evaluator hooks that can emit
  `eval.started`, `eval.completed`, and `eval.failed` events.

### Slice 13 — OpenAI Agents SDK-backed AgentProvider

`OpenAICloudAgentProvider` now targets OpenAI's current code-first Agents SDK
instead of staying a pure cloud-agent scaffold. The adapter supports injected
clients for deterministic tests and can lazy-load the optional `openai-agents`
package from the new `openai-agents` extra.

- Adds an SDK adapter for `Agent` / `Runner.run_streamed` sessions, normalized
  streaming events, follow-up turns, cancellation, HITL approval pause/resume
  through `RunState`, and result/provider-reference artifacts.
- Updates capability honesty so OpenAI Agents SDK and Claude Code advertise
  implemented session lifecycles while Vertex Agent Engine remains the honest
  unsupported provider.
- Adds offline fake-SDK tests for lifecycle streaming, continuation,
  cancellation, approval resume, artifact listing, and event replay.

### Slice 12 — Claude Agent SDK-backed AgentProvider

`ClaudeCodeAgentProvider` is now the first non-local `AgentProvider` with a
real SDK-backed lifecycle. In addition to the existing injected-client path, it
can lazy-load the optional `claude-agent-sdk` package from the new
`claude-agent` extra and supervise sessions through the runtime contract.

- Adds a production SDK adapter that creates agents, starts SDK sessions,
  streams normalized model/workspace/session events, forwards follow-up
  messages, interrupts sessions for cancellation, maps permission callbacks to
  `approval.requested`, and exposes collected result/file-change artifacts.
- Updates capability honesty so Claude Code advertises sessions, streaming,
  artifacts, workspace, approvals, MCP, cancellation, and resume metadata while
  OpenAI cloud and Vertex Agent Engine remain honest stubs at that slice.
- Adds offline fake-SDK tests for SDK-backed lifecycle and approval resume.

### Slice 11 — JSONL/SQLite stores + resume-from-saved-state (M5 starter)

First non-memory persistence backends for the runtime, plus the wiring
to resume an in-flight run from disk. Closes VALIDATION row 5.7.

Persistence:
- `JSONLEventStore` — append-only file-backed `EventStore`. One JSON
  object per line. `raw` payloads are dropped by default (portable across
  processes) but can be retained with `keep_raw=True`. Concurrent writers
  in one process are serialized via an `asyncio.Lock`.
- `SQLiteRunStore` — SQLite-backed `RunStore` with a single
  `runs(session_id PK, provider, model, payload, updated_at)` table.
  `provider` and `model` are surfaced as columns for cheap filtering;
  full state lives in a JSON blob. Async wrapper over stdlib `sqlite3`.
  Adds `list_runs()` and `close()`.

Serialization (`core/serialization.py`):
- Safe `to_dict` / `from_dict` round-trips for `AgentEvent`, `RunItem`,
  `ProviderState`, `RunState`, and `RawEnvelope`.
- `_kind` discriminator on serialized dataclasses keeps deserialization
  type-aware. Unknown values pass through `repr` so output is always
  JSON-decodable.

Resume:
- `AgentRuntime.run` and `AgentRuntime.stream` now accept an optional
  `provider_state` so apps can hand back state loaded from a `RunStore`.
  Plumbed through the `AgentLoop` to the model adapter as a real
  `TurnRequest.provider_state`.

Tests:
- `tests/unit/test_stores_persistent.py` — 9 tests: typed-payload
  round-trip, raw drop/keep, JSONL append/list/limit/sequence cursor,
  cross-instance JSONL persistence, run-id-less event drop, SQLite save/
  load/list, cross-instance SQLite persistence, missing-key load.
- `tests/runtime/test_resume_run_from_saved_state.py` — 2 tests proving
  end-to-end resume: save state to SQLite, reload from a fresh
  `AgentRuntime`, finish the work, and assert the second turn's
  `TurnRequest.provider_state` carries the resumed continuation IDs.
- 109 → 120 passing offline; ruff + mypy --strict clean.

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
- Re-exported from `blackbox.observability`.

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

- New data classes in `blackbox.workspaces`:
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
- New `blackbox.loop.AgentLoop` owns the iterative control flow per
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
