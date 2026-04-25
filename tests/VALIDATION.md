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

1. ✅ `unit/test_tool_runtime.py::test_tool_runtime_injects_context_without_schema_visibility`
2. ✅ `unit/test_echo_model.py::test_echo_model_runtime_stream`
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
| 1.1 | ✅ | Plain LLM call | `unit/test_echo_model.py::test_echo_model_runtime_run` | Runtime can call a model and return final text. |
| 1.2 | ✅ | Streaming LLM call | `unit/test_echo_model.py::test_echo_model_runtime_stream` | Runtime emits `model.text.delta` events and collects final text. |
| 1.3 | ✅ | Tool call request | scripted-model fixture in `runtime/` | Provider emits a normalized `tool.call.requested` event. |
| 1.4 | ✅ | Single tool call loop | `runtime/test_runtime_run.py::test_run_dispatches_a_single_tool_and_returns_final_text` | Runtime detects one tool call, executes it, sends result back, finishes. |
| 1.5 | ✅ | Multiple tool calls | `runtime/test_runtime_run.py::test_run_dispatches_three_tools_in_one_turn` | Runtime executes multiple tool calls in one turn. |
| 1.6 | ✅ | Parallel tool calls | `runtime/test_runtime_run.py::test_run_executes_parallel_tool_calls_concurrently` | Multiple tool calls from one model turn execute concurrently and feed results back in request order. |
| 1.7 | ✅ | Tool-call error | `runtime/test_local_agent_provider.py` (denial path) | Tool failures convert into typed runtime/tool events. |
| 1.8 | ✅ | Structured output | `runtime/test_runtime_run.py::test_run_validates_pydantic_output_type` | Runtime validates final output against a schema. |
| 1.9 | ✅ | Structured output retry | `runtime/test_runtime_retry.py` (5 tests) | Runtime can repair invalid structured output via repair prompt up to `max_validation_retries`. |
| 1.9a | ✅ | Provider-native structured output | `unit/test_output_schema.py`, `unit/test_model_request_controls.py`, `runtime/test_provider_native_output.py` | Runtime converts schemas, wires OpenAI/Gemini native schema controls, gates provider capability, and validates returned structured JSON locally. |
| 1.9b | ✅ | Finalizer tool structured output | `runtime/test_provider_native_output.py::test_finalizer_tool_strategy_returns_validated_output`, `::test_provider_native_can_fallback_to_finalizer_tool` | Runtime injects a hidden final output tool, terminates on the tool call, and validates arguments as the final typed output. |
| 1.10 | ✅ | Provider state continuation | `unit/test_state.py` | Runtime preserves and reuses `ProviderState`. |
| 1.11 | ✅ | Cancellation | `runtime/test_local_agent_provider.py::test_cancel_between_turns` | Runtime can cancel a running model/agent loop. |
| 1.12 | ✅ | Raw provider payload | `golden/openai/test_responses_event_mapping.py::test_hosted_tool_falls_back_to_generic_item_event`, `golden/anthropic/test_messages_event_mapping.py::test_hosted_block_falls_back_to_generic_item_event` | Events keep raw provider data safely. |
| 1.13 | ✅ | Anthropic Messages adapter parity | `golden/anthropic/test_messages_event_mapping.py` (8 tests) | Second real provider maps streaming events without flattening to chat. |
| 1.14 | ✅ | Gemini GenerateContent adapter parity | `golden/gemini/test_generate_content_event_mapping.py`, `integration/gemini/test_generate_content_smoke.py` | Third real provider maps streaming events without flattening to chat; live smoke is gated by `GOOGLE_API_KEY`. |
| 1.15 | ✅ | xAI Responses adapter parity | `golden/openai/test_responses_event_mapping.py::test_openai_compatible_subclass_preserves_provider_identity`, `integration/xai/test_responses_smoke.py` | OpenAI-compatible xAI Responses maps events and provider state under the `xai` provider identity; live smoke is gated by `XAI_API_KEY`. |
| 1.16 | ✅ | Provider-native request controls | `unit/test_model_request_controls.py` | Common request controls map to OpenAI/xAI/Anthropic/Gemini native kwargs while provider `extra` remains an override escape hatch. |
| 1.17 | ✅ | Model usage and cost accounting | `unit/test_model_accounting.py` | Provider usage payloads normalize into result metadata and optional catalog pricing produces cost estimates without hard-coded prices. |
| 1.18 | ✅ | Chat compatibility projection | `unit/test_chat_compat.py` | Chat-shaped messages can be projected into model runtime input through an explicit facade without replacing provider-native internals. |
| 1.19 | ✅ | Hosted tool specs | `unit/test_hosted_tools.py`, `unit/test_model_request_controls.py::test_openai_responses_maps_hosted_tools_to_tools_and_include`, `::test_gemini_maps_web_search_hosted_tool_to_config`, `::test_xai_only_accepts_raw_hosted_tools`, `runtime/test_runtime_run.py::test_run_forwards_hosted_tools_separately_from_local_tools` | Typed hosted tools map to provider-native payloads and stay separate from local function tools. |

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
| 2.1 | ✅ | Register function tool | `unit/test_tool_runtime.py` | Normal Python function becomes a callable tool. |
| 2.2 | 🟡 | Register async tool | `unit/test_tool_runtime.py::test_timeout_raises` (async fn) | Async tools execute correctly. |
| 2.3 | ✅ | Tool schema generation | `unit/test_tool_runtime.py` (provider_tools assertion) | Tool args are exposed correctly to model/provider. |
| 2.4 | ✅ | Manual schema override | same | Custom JSON schema works. |
| 2.5 | ✅ | Context injection | `unit/test_tool_runtime.py::test_tool_runtime_injects_context_without_schema_visibility` | Private values like `user_id`, `db`, `tenant_id` are injected. |
| 2.6 | ✅ | Context not exposed | same | Injected params are not visible to the model. |
| 2.7 | ✅ | Tool payload separation | `runtime/test_runtime_run.py::test_run_collects_deferred_payloads_from_each_tool` | Tool returns LLM-facing `content` and app-facing `payload`. |
| 2.8 | ✅ | Tool timeout | `unit/test_tool_runtime.py::test_timeout_raises` | Long-running tools fail safely. |
| 2.9 | ✅ | Tool concurrency | `unit/test_tool_runtime.py::test_concurrency_cap_serializes_calls` | Multiple tools can run safely together. |
| 2.10 | ✅ | Mock execution | `unit/test_tool_runtime.py::test_mock_call_short_circuits_real_function` | Tests can run without real side effects. |
| 2.11 | ✅ | Tool policy allow | `runtime/test_runtime_run.py::test_policy_allow_dispatches_tool` | Policy layer allows safe tool execution after explicit allow verdict. |
| 2.12 | ✅ | Tool policy deny | `runtime/test_runtime_run.py::test_policy_deny_short_circuits_tool_dispatch` | Policy layer blocks dangerous calls. |
| 2.13 | ✅ | Tool approval required | `runtime/test_local_agent_provider.py::test_approval_pause_and_approve` | Runtime pauses before sensitive tools. |
| 2.14 | ✅ | Dynamic tool session | `runtime/test_runtime_run.py::test_run_uses_dynamic_tool_session_without_global_registration`, `unit/test_tool_runtime.py::test_tool_registry_clone_is_isolated` | One run can attach ad hoc tools from an isolated registry without mutating global tools. |
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
| 4.5 | ✅ | Send message creates invocation | `runtime/test_local_agent_provider.py::test_send_message_updates_session_and_returns_invocation_ref` | Existing sessions can receive more work and return an `InvocationRef`. |
| 4.6 | ✅ | Cancel session | `runtime/test_local_agent_provider.py::test_cancel_between_turns` | Cancellation changes state and emits event. |
| 4.7 | ✅ | List artifacts | `contracts/test_capability_honesty.py::test_local_agent_artifacts_default_empty_page` | Artifacts attached to sessions. |
| 4.8 | ✅ | Agent approval | `runtime/test_local_agent_provider.py::test_approval_pause_and_approve` | Sessions pause/resume for approval. |
| 4.9 | ✅ | Provider raw data | `golden/openai/test_responses_event_mapping.py` | Provider data is preserved. |
| 4.10 | ✅ | Capability enforcement | `contracts/test_capability_honesty.py` | Unsupported actions raise typed errors. |
| 4.11 | ✅ | Cloud-agent scaffold guardrails | `contracts/test_capability_honesty.py::test_cloud_agent_stub_operations_raise_unsupported_when_configured` | Configured cloud-agent scaffolds raise typed unsupported errors until real lifecycle support lands. |

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
| 5.1 | ✅ | Event IDs | `unit/test_data_models.py::test_agent_event_defaults_and_unique_ids` | Every event is traceable. |
| 5.2 | ✅ | Event sequence numbers | `runtime/test_runtime_stream.py::test_every_event_carries_run_id_and_monotonic_sequence` | Events can be replayed in order. |
| 5.3 | ✅ | Event append/list | `unit/test_stores.py::test_in_memory_event_store_round_trip`, `unit/test_stores_persistent.py` (JSONL) | Event store works (in-memory + JSONL). |
| 5.4 | ✅ | Run state save/load | `unit/test_stores.py::test_in_memory_run_store_round_trip`, `unit/test_stores_persistent.py` (SQLite) | Runtime state can be restored (in-memory + SQLite). |
| 5.5 | ✅ | Provider state save/load | `unit/test_state.py::test_run_returns_provider_state` + `accepts_provider_state_for_continuation` | Native continuation state survives round-trip. |
| 5.6 | ✅ | Session state transitions | `runtime/test_local_agent_provider.py` (running → waiting → completed) | Session lifecycle is valid. |
| 5.7 | ✅ | Resume from state | `runtime/test_resume_run_from_saved_state.py` (2 tests) | Save `RunState` to SQLite, reload from a fresh runtime, resume the loop. |
| 5.8 | ✅ | Raw redaction | `unit/test_event_sinks.py` (9 tests) | `RedactingEventSink` rewrites sensitive `RawEnvelope` payloads before forwarding. |

---

## 6. Workspace functions (M2)

For coding-agent workflows. v0.1 ships a `WorkspaceRuntime` that backs
`"local"` workspaces; cloud/sandbox/git kinds remain ⏳.

```python
ws = await runtime.open(WorkspaceSpec.local(path))
await runtime.read_file(ws, path)
await runtime.write_file(ws, path, content)
await runtime.delete_file(ws, path)
await runtime.apply_patch(ws, patch)
await runtime.run_command(ws, CommandSpec(...))
await runtime.snapshot(ws)
```

| # | Status | Functionality | Test | What it proves |
|---|---|---|---|---|
| 6.1 | ✅ | Open local workspace | `unit/test_workspace_runtime.py::test_open_local_workspace_returns_ref` | Spec validates and returns a stable ref. |
| 6.2 | ✅ | Reject non-local kinds | `::test_open_rejects_non_local_workspace_kind` | Cloud/sandbox/git raise `WorkspaceError` until adapters land. |
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
| 6.15 | ✅ | Command timeout | `unit/test_workspace_runtime.py::test_run_command_timeout_sets_timed_out` | Long commands fail with `timed_out=True`. |
| 6.16 | ✅ | AgentLoop integration | `runtime/test_runtime_run.py::test_workspace_tools_run_through_agent_loop`, `::test_workspace_write_command_and_snapshot_tools_run_through_agent_loop` | Workspace read/write/command/snapshot tools can be registered and invoked through the high-level loop. |

---

## 7. MCP functions (M3)

For first-class MCP support.

```python
connector.list_tools(...)
connector.call_tool(...)
```

| # | Status | Functionality | Test | What it proves |
|---|---|---|---|---|
| 7.1 | ✅ | Namespaced list_tools | `unit/test_mcp_connector.py::test_mcp_connector_lists_namespaced_tools` | Local MCP tools get stable `mcp:<server>.<tool>` refs. |
| 7.2 | ✅ | Local MCP call dispatch | `::test_mcp_connector_calls_registered_tool_and_emits_events` | Registered tools execute and emit started/completed events. |
| 7.3 | ✅ | `before_mcp_call` deny | `::test_mcp_connector_gates_calls_with_policy` | Policy can block MCP calls with typed `MCPError`. |
| 7.4 | ✅ | MCP approval required | `::test_mcp_connector_surfaces_required_approval` | Required approval emits `MCP_APPROVAL_REQUIRED` and raises `ApprovalError`. |
| 7.5 | ⏳ | MCP transports | `unit/test_mcp_connector.py::test_mcp_server_spec_represents_prd_transport_names` covers spec names only | stdio + HTTP/SSE + streamable HTTP process/client management. |
| 7.6 | ⏳ | Provider-native remote MCP | not yet | Provider adapters receive remote MCP server configuration. |
