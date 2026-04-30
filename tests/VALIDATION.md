# Test Validation Coverage

This document maps every behavior the runtime must validate to the test that
proves it. Use it as a coverage tracker, not a wishlist: every row should land
in one of the existing test directories under `tests/`.

Status legend:

- ✅ shipped and asserted by a current test
- 🟡 partial (covered indirectly, or has a test but with caveats)
- ⏳ planned (not in v0.1; waits on a later milestone)

Test layout: see `tests/` for `unit/`, `runtime/`, `contracts/`, `golden/`,
`integration/`, `e2e/`.

---

## Minimal validation order

When new behavior lands, write the tests in this order. It's the shortest
path that proves the runtime is not just a provider abstraction but the
actual product loop.

1. ✅ `unit/tools/test_tool_runtime.py::test_tool_runtime_injects_context_without_schema_visibility`
2. ✅ `unit/providers/model_adapters/test_echo_model.py::test_echo_model_runtime_stream`
3. ✅ Tool-call request emission — covered via scripted-model fixture
4. ✅ `runtime/test_runtime_run.py::test_run_dispatches_a_single_tool_and_returns_final_text`
5. ✅ `runtime/test_runtime_run.py::test_run_dispatches_three_tools_in_one_turn`
6. ✅ `runtime/test_runtime_run.py::test_run_injects_tool_execution_context_without_schema_visibility`
7. ✅ `runtime/test_runtime_run.py::test_run_validates_pydantic_output_type`
8. ✅ `runtime/test_runtime_stream.py::test_run_and_stream_yield_correlated_run_ids`
9. ✅ `runtime/test_local_agent_provider.py::test_approval_pause_and_approve`
10. ✅ `runtime/test_resume_run_from_saved_state.py` — proves persisted state can resume a fresh runtime

That sequence proves: call model → detect tool calls → execute tools →
inject private context → continue model → validate typed output → emit
events → preserve state → pause/resume when needed.

---

## 1. Model runtime functions

These are the primitive model-call cases.

```python
await runtime.models.run(...)
await runtime.models.stream(...)
```

| # | Status | Functionality | Test | What it proves |
|---|---|---|---|---|
| 1.1 | ✅ | Plain LLM call | `unit/providers/model_adapters/test_echo_model.py::test_echo_model_runtime_run` | Runtime can call a model and return final text. |
| 1.2 | ✅ | Streaming LLM call | `unit/providers/model_adapters/test_echo_model.py::test_echo_model_runtime_stream` | Runtime emits `model.text.delta` events and collects final text. |
| 1.3 | ✅ | Tool call request | scripted-model fixture in `runtime/` | Provider emits a normalized `tool.call.requested` event. |
| 1.4 | ✅ | Single tool call loop | `runtime/test_runtime_run.py::test_run_dispatches_a_single_tool_and_returns_final_text` | Runtime detects one tool call, executes it, sends result back, finishes. |
| 1.5 | ✅ | Multiple tool calls | `runtime/test_runtime_run.py::test_run_dispatches_three_tools_in_one_turn` | Runtime executes multiple tool calls in one turn. |
| 1.6 | ✅ | Parallel tool calls | `runtime/test_runtime_run.py::test_run_executes_parallel_tool_calls_concurrently` | Multiple tool calls from one model turn execute concurrently and feed results back in request order. |
| 1.7 | ✅ | Tool-call error | `runtime/test_local_agent_provider.py` (denial path) | Tool failures convert into typed runtime/tool events. |
| 1.8 | ✅ | Structured output | `runtime/test_runtime_run.py::test_run_validates_pydantic_output_type` | Runtime validates final output against a schema. |
| 1.9 | ✅ | Structured output retry | `runtime/test_runtime_retry.py` (5 tests) | Runtime can repair invalid structured output via repair prompt up to `max_validation_retries`. |
| 1.9a | ✅ | Provider-native structured output | `unit/output/test_output_schema.py`, `unit/providers/model_adapters/test_model_request_controls.py`, `runtime/test_provider_native_output.py` | Runtime converts schemas, wires OpenAI/Gemini native schema controls, gates provider capability, and validates returned structured JSON locally. |
| 1.9b | ✅ | Finalizer tool structured output | `runtime/test_provider_native_output.py::test_finalizer_tool_strategy_returns_validated_output`, `::test_provider_native_can_fallback_to_finalizer_tool` | Runtime injects a hidden final output tool, terminates on the tool call, and validates arguments as the final typed output. |
| 1.9c | ✅ | Dict JSON Schema post-hoc parsing | `runtime/test_provider_native_output.py::test_posthoc_parse_validates_dict_json_schema`, `::test_posthoc_parse_rejects_invalid_dict_json_schema_output` | Raw JSON Schema dicts are useful outside provider-native mode and reject invalid final JSON with `OutputValidationError`. |
| 1.10 | ✅ | Provider state continuation | `unit/core/test_state.py` | Runtime preserves and reuses `ProviderState`. |
| 1.11 | ✅ | Cancellation | `runtime/test_local_agent_provider.py::test_cancel_between_turns` | Runtime can cancel a running model/agent loop. |
| 1.12 | ✅ | Raw provider payload | `golden/openai/test_responses_event_mapping.py::test_unknown_hosted_tool_falls_back_to_generic_item_event`, `golden/anthropic/test_messages_event_mapping.py::test_hosted_block_falls_back_to_generic_item_event` | Events keep raw provider data safely. |
| 1.13 | ✅ | Anthropic Messages adapter parity | `golden/anthropic/test_messages_event_mapping.py` (8 tests) | Second real provider maps streaming events without flattening to chat. |
| 1.14 | ✅ | Gemini GenerateContent adapter parity | `golden/gemini/test_generate_content_event_mapping.py`, `integration/gemini/test_generate_content_smoke.py` | Third real provider maps streaming events without flattening to chat; live smoke is gated by `GOOGLE_API_KEY`. |
| 1.15 | ✅ | xAI Responses adapter parity | `golden/openai/test_responses_event_mapping.py::test_openai_compatible_subclass_preserves_provider_identity`, `integration/xai/test_responses_smoke.py` | OpenAI-compatible xAI Responses maps events and provider state under the `xai` provider identity; live smoke is gated by `XAI_API_KEY`. |
| 1.16 | ✅ | Provider-native request controls | `unit/providers/model_adapters/test_model_request_controls.py` | Common request controls map to OpenAI/xAI/Anthropic/Gemini native kwargs while provider `extra` remains an override escape hatch. OpenAI typed coverage includes tool-search control and compaction/truncation. |
| 1.17 | ✅ | Model usage and cost accounting | `unit/core/test_model_accounting.py`, `integration/openai/test_cache_and_cost.py`, `integration/gemini/test_cache_and_cost.py`, `integration/xai/test_cost_tracking.py` | Provider usage payloads normalize into result metadata, split cache read/cache creation tokens where available, count tool calls, preserve provider details, and optional catalog pricing produces cost estimates without hard-coded prices. |
| 1.18 | ✅ | Chat compatibility projection | `unit/compat/test_chat_compat.py` | Chat-shaped messages can be projected into model runtime input through an explicit facade without replacing provider-native internals. |
| 1.19 | ✅ | Hosted tool specs | `unit/tools/test_hosted_tools.py`, `unit/providers/model_adapters/test_model_request_controls.py::test_openai_responses_maps_hosted_tools_to_tools_and_include`, `::test_gemini_maps_web_search_hosted_tool_to_config`, `::test_xai_only_accepts_raw_hosted_tools`, `runtime/test_runtime_run.py::test_run_forwards_hosted_tools_separately_from_local_tools` | Typed hosted tools map to provider-native payloads and stay separate from local function tools. |
| 1.20 | ✅ | Hosted tool event normalization | `golden/openai/test_responses_event_mapping.py::test_hosted_tool_maps_to_typed_run_item`, `::test_unknown_hosted_tool_falls_back_to_generic_item_event` | Known hosted provider items become typed hosted-tool run items and hosted-tool result metadata, while unknown provider items keep raw fallback behavior. |
| 1.21 | ✅ | Provider-native remote MCP | `unit/tools/test_hosted_tools.py::test_remote_mcp_serializes_for_openai`, `::test_remote_mcp_serializes_for_anthropic`, `unit/providers/model_adapters/test_model_request_controls.py::test_openai_responses_maps_remote_mcp_to_tool`, `::test_anthropic_maps_remote_mcp_to_server_and_toolset` | Remote MCP has a typed hosted-tool API and provider-native OpenAI/Anthropic request mapping. |
| 1.22 | ✅ | MCP event and cache metadata | `golden/openai/test_responses_event_mapping.py::test_mcp_items_include_typed_run_item_data`, `golden/anthropic/test_messages_event_mapping.py::test_mcp_blocks_map_to_typed_run_items`, `unit/core/test_model_accounting.py::test_model_runtime_collects_mcp_cache_metadata`, `::test_agent_runtime_result_metadata_includes_mcp_cache_metadata` | MCP list/call/approval items become typed run items and result metadata exposes cacheable tool-list context IDs. |
| 1.23 | ✅ | Live OpenAI model loop smoke | `integration/openai/test_responses_smoke.py` | Real Responses calls cover text streaming, provider-state continuation, provider-native structured output through `runtime.run`, and local tool dispatch. |
| 1.24 | ✅ | Live Gemini stream shape | `golden/gemini/test_generate_content_event_mapping.py::test_awaitable_stream_produces_text_and_provider_state`, `integration/gemini/test_generate_content_smoke.py` | Adapter handles both direct async streams and SDK awaitable stream handles. |
| 1.25 | ✅ | Native provider cache controls and registry | `unit/providers/model_adapters/test_model_request_controls.py`, `unit/core/test_model_accounting.py`, `unit/core/test_provider_cache.py`, `integration/openai/test_cache_and_cost.py`, `integration/anthropic/test_cache_and_cost.py`, `integration/gemini/test_cache_and_cost.py` | Runtime accepts typed cache controls, maps them to provider-native cache fields, reports cache-hit metrics, persists cache registry records in memory or SQLite, supports invalidation/expired-entry eviction, and creates/deletes Gemini context caches where the provider API is available. Live cache tests are network-gated and skip when provider credentials or cache resources are absent. |
| 1.26 | ✅ | Model-turn trace metadata | `unit/observability/test_trace_spans.py`, `golden/openai/test_responses_event_mapping.py::test_hosted_tool_maps_to_typed_run_item` | Runtime result metadata includes compact model-turn spans carrying timing plus usage, cost, MCP, and hosted-tool attributes when available. |
| 1.27 | ✅ | Granular model capability profiles | `unit/providers/model_adapters/test_model_capability_profiles.py`, `contracts/test_capability_honesty.py::test_model_providers_return_granular_capability_profiles` | Runtime exposes `ModelCapabilityProfile` for provider/model hosted tools, output strategies, controls, state modes, constraints, and conservative derivation from legacy `ModelCapabilities`. |
| 1.28 | ✅ | Capability validation before provider call | `runtime/test_model_capability_validation.py` | Unsupported typed hosted tools, explicit controls, and explicit state modes raise `UnsupportedFeatureError` before adapter dispatch; raw hosted-tool passthrough remains allowed when advertised. |
| 1.29 | ✅ | Granular output fallback resolution | `runtime/test_model_capability_validation.py::test_runtime_applies_output_fallback`, `unit/providers/model_adapters/test_model_capability_profiles.py::test_resolve_output_strategy_applies_supported_fallback` | `OutputSpec.fallback` is resolved against granular output strategy support before dispatch. |
| 1.30 | ✅ | OpenAI hosted tool hardening | `unit/providers/model_adapters/test_model_request_controls.py::test_openai_maps_tool_search_control_to_tool`, `runtime/test_hosted_tool_loop.py::test_runtime_executes_computer_use_through_handler_and_continues`, `golden/openai/test_responses_event_mapping.py::test_image_generation_item_extracts_artifact` | Tool search can be requested through typed controls, computer-use continuation is exercised through hosted handlers, and image-generation output can produce artifacts. |
| 1.31 | ✅ | Model-specific hosted-tool and structured-output capability contracts | `unit/providers/model_adapters/test_model_capability_profiles.py::test_anthropic_profile_is_model_version_specific`, `::test_gemini_profile_gates_structured_output_tool_combinations_by_model`, `contracts/test_capability_honesty.py::test_model_runtime_rejects_unsupported_provider_native_before_sdk_call`, `::test_model_runtime_rejects_unsupported_profile_constraint_before_sdk_call` | Provider profiles distinguish model versions and unsupported output/tool combinations fail before SDK dispatch. |
| 1.32 | ✅ | Provider tool/source state preservation | `golden/openai/test_hosted_tools_v2_event_mapping.py`, `golden/anthropic/test_messages_event_mapping.py::test_mcp_blocks_map_to_typed_run_items`, `golden/gemini/test_generate_content_event_mapping.py::test_provider_state_preserves_tool_ids_sources_and_file_handles` | `ProviderState` retains provider-native tool IDs, hosted/MCP state, output item IDs, source references, and file handles needed for continuation/audit. |

---

## 2. Local tool runtime functions

```python
runtime.tools.register(...)
await runtime.tools.call(...)
runtime.tools.all_tools(...)
runtime.tools.get(...)
```

| # | Status | Functionality | Test | What it proves |
|---|---|---|---|---|
| 2.1 | ✅ | Register function tool | `unit/tools/test_tool_runtime.py` | Normal Python function becomes a callable tool. |
| 2.2 | 🟡 | Register async tool | `unit/tools/test_tool_runtime.py::test_timeout_raises` (async fn) | Async tools execute correctly. |
| 2.3 | ✅ | Tool schema generation | `unit/tools/test_tool_runtime.py` (provider_tools assertion) | Tool args are exposed correctly to model/provider. |
| 2.4 | ✅ | Manual schema override | same | Custom JSON schema works. |
| 2.5 | ✅ | Context injection | `unit/tools/test_tool_runtime.py::test_tool_runtime_injects_context_without_schema_visibility` | Private values like `user_id`, `db`, `tenant_id` are injected. |
| 2.6 | ✅ | Context not exposed | same | Injected params are not visible to the model. |
| 2.7 | ✅ | Tool payload separation | `runtime/test_runtime_run.py::test_run_collects_deferred_payloads_from_each_tool` | Tool returns LLM-facing `content` and app-facing `payload`. |
| 2.8 | ✅ | Tool timeout | `unit/tools/test_tool_runtime.py::test_timeout_raises` | Long-running tools fail safely. |
| 2.9 | ✅ | Tool concurrency | `unit/tools/test_tool_runtime.py::test_concurrency_cap_serializes_calls` | Multiple tools can run safely together. |
| 2.10 | ✅ | Mock execution | `unit/tools/test_tool_runtime.py::test_mock_call_short_circuits_real_function` | Tests can run without real side effects. |
| 2.11 | ✅ | Tool policy allow | `runtime/test_runtime_run.py::test_policy_allow_dispatches_tool` | Policy layer allows safe tool execution after explicit allow verdict. |
| 2.12 | ✅ | Tool policy deny | `runtime/test_runtime_run.py::test_policy_deny_short_circuits_tool_dispatch` | Policy layer blocks dangerous calls. |
| 2.13 | ✅ | Tool approval required | `runtime/test_local_agent_provider.py::test_approval_pause_and_approve` | Runtime pauses before sensitive tools. |
| 2.14 | ✅ | Dynamic tool session | `runtime/test_runtime_run.py::test_run_uses_dynamic_tool_session_without_global_registration`, `unit/tools/test_tool_runtime.py::test_tool_registry_clone_is_isolated` | One run can attach ad hoc tools from an isolated registry without mutating global tools. |
| 2.15 | ✅ | High-level tool operational controls | `runtime/test_runtime_run.py::test_run_can_limit_tool_concurrency`, `::test_run_can_timeout_tool_execution` | The blackbox loop exposes tool concurrency and timeout controls without custom `ToolRuntime` construction. |

---

## 3. High-level blackbox runtime

```python
await runtime.run(...)
async for event in runtime.stream(...):
    ...
```

| # | Status | Functionality | Test | What it proves |
|---|---|---|---|---|
| 3.1 | ✅ | Full text-only run | `runtime/test_runtime_run.py::test_run_returns_text_when_no_output_type` | User gives input, gets final text. |
| 3.2 | ✅ | Full tool loop | `runtime/test_runtime_run.py::test_run_dispatches_a_single_tool_and_returns_final_text` | Runtime owns model → tool → model continuation. |
| 3.3 | ✅ | Multi-tool loop | `runtime/test_runtime_run.py::test_run_dispatches_three_tools_in_one_turn` | Several tools dispatched in one turn. |
| 3.4 | ✅ | Injected context loop | `runtime/test_runtime_run.py::test_run_injects_tool_execution_context_without_schema_visibility` | Tool loop honors private runtime context. |
| 3.5 | ✅ | Structured typed result | `runtime/test_runtime_run.py::test_run_validates_pydantic_output_type` + dataclass test | User gets `AgentResult[T]` with validated output. |
| 3.6 | ✅ | Tool payloads collected | `runtime/test_runtime_run.py::test_run_collects_deferred_payloads_from_each_tool` | App-facing payloads are preserved. |
| 3.7 | ✅ | Event collection | every `runtime/test_runtime_run.py` test | Final result contains all runtime events. |
| 3.8 | ✅ | run uses stream internally | `runtime/test_runtime_stream.py::test_run_and_stream_yield_correlated_run_ids` | `run(...)` is a collector over `stream(...)`. |
| 3.9 | ✅ | Approval pause | `runtime/test_local_agent_provider.py::test_approval_pause_and_approve` | Runtime enters `waiting` when approval needed. |
| 3.10 | ✅ | Approval resume | same | Runtime continues after approval. |
| 3.11 | ✅ | Approval denial | `runtime/test_local_agent_provider.py::test_approval_denial_skips_tool_and_records_failure` | Runtime handles denied actions safely. |
| 3.12 | ✅ | Failure result | `runtime/test_runtime_run.py::test_run_raises_output_validation_error_on_pydantic_mismatch` + max_iterations test | Failures produce typed errors and useful diagnostics. |
| 3.13 | ✅ | Workspace agent package bridge | `unit/workspace_agents/test_workspace_agents.py::test_run_workspace_agent_uses_existing_runtime_loop` | A portable `WorkspaceAgentSpec` can run through the existing high-level runtime loop without a separate product scheduler or UI. |

---

## 3A. Workspace agent package contracts

For governed, shareable agent definitions without downstream product concerns.

```python
WorkspaceAgentSpec(...)
WorkspaceAgentRegistry
run_workspace_agent(...)
```

| # | Status | Functionality | Test | What it proves |
|---|---|---|---|---|
| 3A.1 | ✅ | Package serialization | `unit/workspace_agents/test_workspace_agents.py::test_workspace_agent_spec_serializes_nested_contracts` | Nested connectors, permissions, schedules, and skills round-trip through dict serialization. |
| 3A.2 | ✅ | `AgentSpec` projection | `unit/workspace_agents/test_workspace_agents.py::test_workspace_agent_converts_to_agent_spec_metadata` | Package metadata can be projected into the existing lower-level agent spec. |
| 3A.3 | ✅ | Permission policy context | `unit/workspace_agents/test_workspace_agents.py::test_tool_permission_policy_request_carries_agent_context` | Tool permission declarations can produce policy requests carrying agent/tool/connector scope metadata. |
| 3A.4 | ✅ | Registry protocol implementation | `unit/workspace_agents/test_workspace_agents.py::test_in_memory_workspace_agent_registry_publishes_and_lists` | In-memory registry can save, publish, list, and deprecate package definitions. |
| 3A.5 | ✅ | Runtime preparation guard | `unit/workspace_agents/test_workspace_agents.py::test_prepare_agent_spec_requires_model_provider` | Runtime bridge fails clearly when a model-backed package lacks a provider. |

---

## 4. AgentProvider functions

For local or cloud-managed agents.

```python
await runtime.agents.create_agent(...)
await runtime.agents.create_session(...)
async for event in runtime.agents.stream(...):
    ...
await runtime.agents.send_message(...)
await runtime.agents.approve(...)
await runtime.agents.cancel(...)
await runtime.agents.list_artifacts(...)
```

| # | Status | Functionality | Test | What it proves |
|---|---|---|---|---|
| 4.1 | ✅ | Create local agent | `runtime/test_local_agent_provider.py` | Runtime registers/creates a local agent definition. |
| 4.2 | ✅ | Create session | same | Agent sessions are first-class objects. |
| 4.3 | ✅ | Session starts running | same | Session status transitions work. |
| 4.4 | ✅ | Stream session events | same | Agent emits normalized events. |
| 4.5 | ✅ | Send message resumes session | `runtime/test_local_agent_provider.py::test_send_message_queues_follow_up_and_resumes_provider_state` | Existing sessions can receive more work, return an `InvocationRef`, and continue with the accumulated `ProviderState`. |
| 4.6 | ✅ | Cancel session | `runtime/test_local_agent_provider.py::test_cancel_between_turns` | Cancellation changes state and emits event. |
| 4.7 | ✅ | List artifacts | `contracts/test_capability_honesty.py::test_local_agent_artifacts_default_empty_page` | Artifacts attached to sessions. |
| 4.8 | ✅ | Agent approval | `runtime/test_local_agent_provider.py::test_approval_pause_and_approve` | Sessions pause/resume for approval. |
| 4.9 | ✅ | Provider raw data | `golden/openai/test_responses_event_mapping.py` | Provider data is preserved. |
| 4.10 | ✅ | Capability enforcement | `contracts/test_capability_honesty.py` | Unsupported actions raise typed errors. |
| 4.11 | ✅ | Cloud-agent scaffold guardrails | `contracts/test_capability_honesty.py::test_cloud_agent_stub_operations_raise_unsupported_when_configured` | Vertex cloud-agent scaffold raises typed unsupported errors until real lifecycle support lands. |
| 4.12 | ✅ | Client-backed Claude Code lifecycle | `runtime/test_claude_code_agent_provider.py` | Injected Claude Code clients can create agents, start sessions, stream/resume events, send messages, approve, cancel, and list artifacts. |
| 4.13 | ✅ | SDK-backed Claude Code lifecycle | `runtime/test_claude_code_agent_provider.py::test_claude_code_sdk_backed_provider_runs_without_injected_client` | Optional `claude-agent-sdk` wrapper maps sessions, streaming events, workspace artifacts, approvals, cancellation, and resume metadata through `ClaudeCodeAgentProvider`. |
| 4.14 | ✅ | SDK-backed OpenAI Agents lifecycle | `runtime/test_openai_agents_provider.py` | Optional `openai-agents` wrapper maps Agents SDK sessions, streaming events, approvals, continuation, cancellation, and artifacts through `OpenAICloudAgentProvider`. |

---

## 5. Event/state functions

```python
event_store.append(...)
event_store.list_events(...)

run_store.save(...)
run_store.load(...)
```

| # | Status | Functionality | Test | What it proves |
|---|---|---|---|---|
| 5.1 | ✅ | Event IDs | `unit/core/test_data_models.py::test_agent_event_defaults_and_unique_ids` | Every event is traceable. |
| 5.2 | ✅ | Event sequence numbers | `runtime/test_runtime_stream.py::test_every_event_carries_run_id_and_monotonic_sequence` | Events can be replayed in order. |
| 5.3 | ✅ | Event append/list | `unit/core/test_stores.py::test_in_memory_event_store_round_trip`, `unit/core/test_stores_persistent.py` (JSONL) | Event store works (in-memory + JSONL). |
| 5.4 | ✅ | Run state save/load | `unit/core/test_stores.py::test_in_memory_run_store_round_trip`, `unit/core/test_stores_persistent.py` (SQLite) | Runtime state can be restored (in-memory + SQLite). |
| 5.5 | ✅ | Provider state save/load | `unit/core/test_state.py::test_run_returns_provider_state` + `accepts_provider_state_for_continuation` | Native continuation state survives round-trip. |
| 5.6 | ✅ | Session state transitions | `runtime/test_local_agent_provider.py` (running → waiting → completed) | Session lifecycle is valid. |
| 5.7 | ✅ | Resume from state | `runtime/test_resume_run_from_saved_state.py` (2 tests) | Save `RunState` to SQLite, reload from a fresh runtime, resume the loop. |
| 5.8 | ✅ | Raw redaction | `unit/observability/test_event_sinks.py` (9 tests) | `RedactingEventSink` rewrites sensitive `RawEnvelope` payloads before forwarding. |
| 5.9 | ✅ | Workflow trace context | `runtime/test_runtime_stream.py::test_every_event_carries_trace_context_and_tool_span_is_shared` | Every runtime event carries trace/span context and related tool events share a span. |
| 5.10 | ✅ | Replay, diff, eval, OTEL projection | `unit/observability/test_observability_workflows.py` | Stored events reconstruct traces; runs can be diffed; evaluators emit eval events; spans convert to OpenTelemetry attributes. |
| 5.11 | ✅ | Production observability preset | `unit/observability/test_presets.py`, `unit/observability/test_metrics.py`, `runtime/test_observability_presets.py` | `ObservabilityPreset.production(...)` enables redacted event logging, trace export, standardized metrics, runtime fan-out, provider/session correlation attributes, and replay/diff trace links. |

---

## 6. Workspace functions (M2)

For coding-agent workflows. v0.1 ships a first-class `runtime.workspaces`
facade over `WorkspaceProvider` backends. Local, git, sandbox, Docker, and
cloud workspace refs share one contract; the workspace-tool bridge remains as
a compatibility adapter for model-tool loops.

```python
workspace = await runtime.workspaces.open(WorkspaceSpec.git(url="...", ref="main"))
result = await runtime.agents.run(
    provider="openai-agents",
    agent="coding-agent",
    task="Implement the feature and return a patch",
    workspace=workspace,
)
```

| # | Status | Functionality | Test | What it proves |
|---|---|---|---|---|
| 6.1 | ✅ | Open local workspace | `unit/workspaces/test_workspace_runtime.py::test_open_local_workspace_returns_ref` | Spec validates and returns a stable ref. |
| 6.2 | ✅ | Local provider rejects wrong kind | `::test_open_rejects_non_local_workspace_kind` | The local provider remains honest and rejects non-local specs. |
| 6.3 | ✅ | File read events | `::test_read_file_emits_workspace_file_read` | Reads emit `WORKSPACE_FILE_READ`. |
| 6.4 | ✅ | File write events | `::test_write_file_emits_change_and_returns_file_change` | Writes emit `WORKSPACE_FILE_CHANGED` with a `FileChange`. |
| 6.5 | ✅ | Patch lifecycle | `::test_apply_patch_emits_per_change_events_and_creates_patch_artifact` | Per-change events + `WORKSPACE_PATCH_CREATED` + `ARTIFACT_CREATED`. |
| 6.6 | ✅ | Path escape rejected | `::test_path_escape_is_rejected` | Relative paths cannot leave the root. |
| 6.7 | ✅ | Command lifecycle | `::test_run_command_emits_started_completed_via_executor` | `WORKSPACE_COMMAND_STARTED` + `WORKSPACE_COMMAND_COMPLETED`. |
| 6.8 | ✅ | Command failure surfaced | `::test_run_command_failure_propagates_exit_code` | Non-zero exit codes carry through. |
| 6.9 | ✅ | `before_workspace_write` deny | `::test_write_file_denied_by_policy_raises_workspace_error` | Policy denies writes with typed error. |
| 6.10 | ✅ | `before_command` deny | `::test_run_command_denied_by_policy_blocks_executor` | Policy denies commands before executor runs. |
| 6.11 | ✅ | `before_artifact_export` deny | `::test_artifact_export_policy_gates_snapshot` | Policy denies snapshot export. |
| 6.12 | ✅ | `require_approval` raises | `::test_write_file_require_approval_raises_approval_error` | Workspace layer surfaces approval gap as `ApprovalError`. |
| 6.13 | ✅ | Snapshot artifact | `::test_snapshot_creates_artifact_and_emits_event` | Snapshots produce typed `Artifact`. |
| 6.14 | ✅ | Artifact pagination | `::test_apply_patch_emits_per_change_events_and_creates_patch_artifact` (uses `list_artifacts`) | `ArtifactPage` returned by filter. |
| 6.15 | ✅ | Command timeout | `unit/workspaces/test_workspace_runtime.py::test_run_command_timeout_sets_timed_out` | Long commands fail with `timed_out=True`. |
| 6.16 | ✅ | AgentLoop integration | `runtime/test_runtime_run.py::test_workspace_tools_run_through_agent_loop`, `::test_workspace_write_command_and_snapshot_tools_run_through_agent_loop` | Workspace read/write/command/snapshot tools can be registered and invoked through the high-level loop. |
| 6.17 | ✅ | First-class workspace facade | `runtime/test_agent_session_run.py::test_agents_run_routes_workspace_ref_through_workspace_provider` | `runtime.workspaces.open(WorkspaceSpec.git(...))` produces a `WorkspaceRef` that routes through `TaskSpec.workspace` into `runtime.agents.run(...)`. |
| 6.18 | ✅ | Provider-native workspace event normalization | `runtime/test_agent_session_run.py::test_provider_native_workspace_events_gain_workspace_context` | Agent providers that bring their own workspace/sandbox events still emit canonical workspace/test events with workspace metadata. |
| 6.19 | ✅ | Docker/cloud provider contracts | `unit/workspaces/test_workspace_provider_contracts.py::test_docker_workspace_provider_uses_docker_kind_contract`, `::test_cloud_workspace_provider_opens_opaque_ref` | Docker and cloud backends share the same `WorkspaceProvider` capability/ref contract. |

---

## 7. MCP functions (M3)

For first-class MCP support.

```python
connector.list_tools(...)
connector.call_tool(...)
```

| # | Status | Functionality | Test | What it proves |
|---|---|---|---|---|
| 7.1 | ✅ | Namespaced list_tools | `unit/mcp/test_mcp_connector.py::test_mcp_connector_lists_namespaced_tools` | Local MCP tools get stable `mcp:<server>.<tool>` refs. |
| 7.2 | ✅ | Local MCP call dispatch | `::test_mcp_connector_calls_registered_tool_and_emits_events` | Registered tools execute and emit started/completed events. |
| 7.3 | ✅ | `before_mcp_call` deny | `::test_mcp_connector_gates_calls_with_policy` | Policy can block MCP calls with typed `MCPError`. |
| 7.4 | ✅ | MCP approval required | `::test_mcp_connector_surfaces_required_approval` | Required approval emits `MCP_APPROVAL_REQUIRED` and raises `ApprovalError`. |
| 7.5 | ✅ | MCP transports | `unit/mcp/test_mcp_connector.py::test_mcp_connector_discovers_and_calls_managed_transport`, `::test_mcp_connector_refreshes_cache_and_stops_transport` | Managed transports initialize, list tools, cache/refresh definitions, call tools, and stop cleanly. |
| 7.6 | ✅ | Provider-native remote MCP | `unit/tools/test_hosted_tools.py::test_remote_mcp_serializes_for_openai`, `::test_remote_mcp_serializes_for_anthropic`, `unit/providers/model_adapters/test_model_request_controls.py::test_openai_responses_maps_remote_mcp_to_tool`, `::test_anthropic_maps_remote_mcp_to_server_and_toolset` | Provider adapters receive remote MCP server configuration. |
| 7.7 | ✅ | MCP runtime tool bridge | `unit/mcp/test_mcp_connector.py::test_mcp_connector_registers_runtime_tool_bridge` | Discovered MCP tools can be registered into a `ToolRegistry` while calls still route through MCP policy/audit handling. |
| 7.8 | ✅ | MCP spec validation/redaction | `unit/mcp/test_mcp_spec.py` | Managed stdio/HTTP requirements, remote local-dispatch trust, tool-filter overlap, and secret redaction are enforced. |
| 7.9 | ✅ | MCP lifecycle negotiation | `unit/mcp/test_mcp_client.py` | Managed clients send `initialize`, validate negotiated protocol versions, send `notifications/initialized`, and normalize tool schemas/annotations. |
| 7.10 | ✅ | MCP toolset routing | `unit/mcp/test_mcp_toolset.py` | `MCPToolset` chooses local or provider-native routing based on mode, transport, capability, URL locality, and policy. |
| 7.11 | ✅ | Runtime MCP toolsets | `runtime/test_mcp_toolset_runtime.py` | `runtime.run(..., toolsets=[...])` exposes local MCP tools as `mcp:<server>.<tool>`, collects MCP payload metadata, and stops connectors. |
| 7.12 | ✅ | MCP protocol compatibility matrix | `unit/mcp/test_mcp_client.py::test_start_negotiates_each_supported_protocol_version`, `::test_protocol_matrix_lists_spec_requested_versions` | Supported MCP protocol versions are declared centrally and every supported version negotiates successfully. |
| 7.13 | ✅ | Remote HTTP MCP auth refresh | `unit/mcp/test_mcp_auth.py` | OAuth bearer providers refresh tokens after auth challenges, and remote auth failures raise `MCPAuthenticationError` without leaking tokens. |
| 7.14 | ✅ | MCP trust policy presets and approval metadata | `unit/mcp/test_mcp_trust_policy.py::test_trust_policy_presets_define_production_postures`, `unit/mcp/test_mcp_connector.py::test_mcp_policy_request_includes_scope_risk_and_trust_metadata` | Production trust presets exist, and MCP approval/policy decisions can inspect server, tool, scopes, risks, route, and trust metadata. |
| 7.15 | ✅ | MCP discovery cache invalidation and auth identity isolation | `unit/mcp/test_mcp_connector.py::test_mcp_discovery_cache_is_isolated_by_auth_identity`, `::test_mcp_list_changed_notification_invalidates_discovery_cache`, `runtime/test_mcp_toolset_runtime.py::test_runtime_toolset_reuses_discovery_cache_between_runs` | Tool-list caches are separated by auth identity, reused by runtime-managed local toolsets across runs, and invalidated when servers emit tool-list change notifications. |
| 7.16 | ✅ | MCP output and error hardening | `unit/mcp/test_mcp_connector.py::test_mcp_call_failed_event_redacts_errors_by_default`, `::test_mcp_tool_policy_output_limit_overrides_server_limit`, `unit/mcp/test_mcp_transports.py` | MCP failure events redact by default, per-tool output limits override server defaults, and transport timeouts issue cancellation notifications. |
| 7.17 | ✅ | Provider-native/local MCP metadata parity | `golden/openai/test_responses_event_mapping.py::test_mcp_items_include_typed_run_item_data`, `golden/anthropic/test_messages_event_mapping.py::test_mcp_blocks_map_to_typed_run_items`, `unit/mcp/test_mcp_connector.py::test_mcp_tool_policy_output_limit_overrides_server_limit` | Provider-native RemoteMCP and local MCP dispatch expose comparable server/tool/ref/route metadata. |
