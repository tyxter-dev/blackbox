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
10. ⏳ `runtime/test_resume_run_from_saved_state` — needs persistence beyond in-memory store

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
| 1.6 | 🟡 | Parallel tool calls | covered as 1.5 (sequential dispatch) | True concurrent execution awaits an OpenAI Responses path with `parallel_tool_calls=True`. |
| 1.7 | ✅ | Tool-call error | `runtime/test_local_agent_provider.py` (denial path) | Tool failures convert into typed runtime/tool events. |
| 1.8 | ✅ | Structured output | `runtime/test_runtime_run.py::test_run_validates_pydantic_output_type` | Runtime validates final output against a schema. |
| 1.9 | ⏳ | Structured output retry | not yet — `OutputSpec.posthoc_parse_with_retry` exists but isn't wired | Runtime can repair or retry invalid structured output. |
| 1.10 | ✅ | Provider state continuation | `unit/test_state.py` | Runtime preserves and reuses `ProviderState`. |
| 1.11 | ✅ | Cancellation | `runtime/test_local_agent_provider.py::test_cancel_between_turns` | Runtime can cancel a running model/agent loop. |
| 1.12 | ✅ | Raw provider payload | `golden/openai/test_responses_event_mapping.py::test_hosted_tool_falls_back_to_generic_item_event`, `golden/anthropic/test_messages_event_mapping.py::test_hosted_block_falls_back_to_generic_item_event` | Events keep raw provider data safely. |
| 1.13 | ✅ | Anthropic Messages adapter parity | `golden/anthropic/test_messages_event_mapping.py` (8 tests) | Second real provider maps streaming events without flattening to chat. |

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
| 2.11 | 🟡 | Tool policy allow | implicit (no policy = allow) | Policy layer allows safe tool execution. |
| 2.12 | ✅ | Tool policy deny | `runtime/test_runtime_run.py::test_policy_deny_short_circuits_tool_dispatch` | Policy layer blocks dangerous calls. |
| 2.13 | ✅ | Tool approval required | `runtime/test_local_agent_provider.py::test_approval_pause_and_approve` | Runtime pauses before sensitive tools. |

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
| 4.5 | 🟡 | Send message creates invocation | `send_message → InvocationRef` is wired but no dedicated test yet | Existing sessions can receive more work. |
| 4.6 | ✅ | Cancel session | `runtime/test_local_agent_provider.py::test_cancel_between_turns` | Cancellation changes state and emits event. |
| 4.7 | ✅ | List artifacts | `contracts/test_capability_honesty.py::test_local_agent_artifacts_default_empty_page` | Artifacts attached to sessions. |
| 4.8 | ✅ | Agent approval | `runtime/test_local_agent_provider.py::test_approval_pause_and_approve` | Sessions pause/resume for approval. |
| 4.9 | ✅ | Provider raw data | `golden/openai/test_responses_event_mapping.py` | Provider data is preserved. |
| 4.10 | ✅ | Capability enforcement | `contracts/test_capability_honesty.py` | Unsupported actions raise typed errors. |

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
| 5.3 | ✅ | Event append/list | `unit/test_stores.py::test_in_memory_event_store_round_trip` | Event store works. |
| 5.4 | ✅ | Run state save/load | `unit/test_stores.py::test_in_memory_run_store_round_trip` | Runtime state can be restored. |
| 5.5 | ✅ | Provider state save/load | `unit/test_state.py::test_run_returns_provider_state` + `accepts_provider_state_for_continuation` | Native continuation state survives round-trip. |
| 5.6 | ✅ | Session state transitions | `runtime/test_local_agent_provider.py` (running → waiting → completed) | Session lifecycle is valid. |
| 5.7 | ⏳ | Resume from state | not yet — needs replay-from-store | Runtime can continue after interruption. |
| 5.8 | ⏳ | Raw redaction | `RawEnvelope` exists but no sink yet to assert behavior | Sensitive raw data handled safely. |

---

## 6. Workspace functions ⏳ (M2)

For coding-agent workflows. None of these exist in v0.1; targets land with M2.

```python
runtime.workspaces.create(...)
runtime.workspaces.read_file(...)
runtime.workspaces.write_file(...)
runtime.workspaces.run_command(...)
runtime.workspaces.create_patch(...)
runtime.workspaces.snapshot(...)
```

Planned coverage: workspace creation, file read/write events, command
execution start/completed/timeout, patch artifact creation, snapshot
artifacts, write-approval gating.

---

## 7. MCP functions ⏳ (M3)

For first-class MCP support.

```python
runtime.mcp.connect(...)
runtime.mcp.list_tools(...)
runtime.mcp.call_tool(...)
runtime.mcp.disconnect(...)
```

Planned coverage: stdio + HTTP/SSE + streamable HTTP transports, list_tools
namespacing, call execution, approval gating, auth-failure typing, raw
event preservation.
