From your note, I would validate the scaffold in two layers:

1. **Model runtime functions**
2. **Agent/runtime loop functions**

Here is a clean validation checklist.

## 1. Model runtime functions to validate

These are the primitive model-call cases.

```python
await runtime.models.run(...)
await runtime.models.stream(...)
```

Validate these scenarios:

| Functionality               | Test name                                | What it proves                                                                |
| --------------------------- | ---------------------------------------- | ----------------------------------------------------------------------------- |
| Plain LLM call              | `test_model_run_text_only`               | Runtime can call a model and return final text.                               |
| Streaming LLM call          | `test_model_stream_text_deltas`          | Runtime emits `model.text.delta` events and collects final text.              |
| Tool call request           | `test_model_emits_tool_call_request`     | Provider can emit a normalized `tool.call.requested` event.                   |
| Single tool call loop       | `test_model_single_tool_call_loop`       | Runtime detects one tool call, executes it, sends result back, and finishes.  |
| Multiple tool calls         | `test_model_multiple_tool_calls_loop`    | Runtime can execute multiple tool calls in one turn or across turns.          |
| Parallel tool calls         | `test_model_parallel_tool_calls`         | Runtime can dispatch multiple independent tool calls concurrently if allowed. |
| Tool-call error             | `test_model_tool_call_error_result`      | Tool failures are converted into typed runtime/tool events.                   |
| Structured output           | `test_model_structured_output`           | Runtime validates final output against a schema.                              |
| Structured output retry     | `test_model_structured_output_retry`     | Runtime can repair or retry invalid structured output.                        |
| Provider state continuation | `test_model_provider_state_continuation` | Runtime preserves and reuses `ProviderState`.                                 |
| Cancellation                | `test_model_run_cancellation`            | Runtime can cancel a running model/agent loop.                                |
| Raw provider payload        | `test_model_events_preserve_raw_payload` | Events keep raw provider data safely.                                         |

---

## 2. Local tool runtime functions to validate

These validate the tool layer independently from models.

```python
runtime.tools.register(...)
await runtime.tools.call(...)
runtime.tools.list(...)
runtime.tools.get(...)
```

Validate:

| Functionality           | Test name                                | What it proves                                                 |
| ----------------------- | ---------------------------------------- | -------------------------------------------------------------- |
| Register function tool  | `test_register_function_tool`            | Normal Python function becomes a callable tool.                |
| Register async tool     | `test_register_async_tool`               | Async tools execute correctly.                                 |
| Tool schema generation  | `test_tool_schema_generation`            | Tool args are exposed correctly to model/provider.             |
| Manual schema override  | `test_tool_schema_override`              | Custom JSON/Pydantic schema works.                             |
| Context injection       | `test_tool_context_injection`            | Private values like `user_id`, `db`, `tenant_id` are injected. |
| Context not exposed     | `test_tool_context_not_in_schema`        | Injected params are not visible to the model.                  |
| Tool payload separation | `test_tool_result_content_payload_split` | Tool returns LLM-facing `content` and app-facing `payload`.    |
| Tool timeout            | `test_tool_timeout`                      | Long-running tools fail safely.                                |
| Tool concurrency        | `test_tool_concurrent_execution`         | Multiple tools can run safely together.                        |
| Mock execution          | `test_tool_mock_execution`               | Tests can run without real side effects.                       |
| Tool policy allow       | `test_tool_policy_allows_call`           | Policy layer can allow safe tool execution.                    |
| Tool policy deny        | `test_tool_policy_denies_call`           | Policy layer can block dangerous calls.                        |
| Tool approval required  | `test_tool_policy_requires_approval`     | Runtime can pause before executing sensitive tools.            |

---

## 3. High-level blackbox runtime functions to validate

This is the most important product contract.

```python
await runtime.run(...)
async for event in runtime.stream(...):
    ...
```

Validate:

| Functionality              | Test name                                           | What it proves                                                     |
| -------------------------- | --------------------------------------------------- | ------------------------------------------------------------------ |
| Full text-only run         | `test_runtime_run_text_only`                        | User can give input and get final text.                            |
| Full tool loop             | `test_runtime_run_single_tool_loop`                 | Runtime owns model → tool → model continuation.                    |
| Multi-tool loop            | `test_runtime_run_multi_tool_loop`                  | Runtime can complete a task requiring several tools.               |
| Injected context loop      | `test_runtime_run_tool_loop_with_context_injection` | Tool loop works with private runtime context.                      |
| Structured typed result    | `test_runtime_run_structured_output`                | User gets `AgentResult[T]` with validated output.                  |
| Tool payloads collected    | `test_runtime_run_collects_tool_payloads`           | App-facing tool payloads are preserved.                            |
| Event collection           | `test_runtime_run_collects_events`                  | Final result contains all runtime events.                          |
| Run uses stream internally | `test_runtime_run_collects_from_stream`             | `run(...)` is a collector over `stream(...)`, not a separate path. |
| Approval pause             | `test_runtime_run_pauses_for_approval`              | Runtime enters `waiting` state when approval is needed.            |
| Approval resume            | `test_runtime_resume_after_approval`                | Runtime continues after approval.                                  |
| Approval denial            | `test_runtime_denies_approved_action`               | Runtime handles denied action safely.                              |
| Failure result             | `test_runtime_run_failure_result`                   | Failures produce typed errors and useful diagnostics.              |

---

## 4. AgentProvider functions to validate

These are for local or cloud-managed agents.

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

Validate:

| Functionality           | Test name                                      | What it proves                                        |
| ----------------------- | ---------------------------------------------- | ----------------------------------------------------- |
| Create local agent      | `test_create_local_agent`                      | Runtime can register/create a local agent definition. |
| Create session          | `test_create_agent_session`                    | Agent sessions are first-class objects.               |
| Session starts running  | `test_agent_session_status_running`            | Session status transitions work.                      |
| Stream session events   | `test_agent_stream_events`                     | Agent emits normalized events.                        |
| Send message to session | `test_agent_send_message_creates_invocation`   | Existing sessions can receive more work.              |
| Cancel session          | `test_agent_cancel_session`                    | Cancellation changes state and emits event.           |
| List artifacts          | `test_agent_list_artifacts`                    | Artifacts are attached to sessions.                   |
| Agent approval          | `test_agent_approval_flow`                     | Agent sessions can pause/resume for approval.         |
| Provider raw data       | `test_agent_events_preserve_raw_payload`       | Cloud/provider data is not lost.                      |
| Capability enforcement  | `test_agent_capability_error_when_unsupported` | Unsupported actions raise `CapabilityError`.          |

---

## 5. Event/state functions to validate

These are the foundation of the new scaffold.

```python
event_store.append(...)
event_store.list_events(...)

run_store.save_run_state(...)
run_store.load_run_state(...)
```

Validate:

| Functionality             | Test name                           | What it proves                                    |
| ------------------------- | ----------------------------------- | ------------------------------------------------- |
| Event IDs                 | `test_events_have_unique_ids`       | Every event is traceable.                         |
| Event ordering            | `test_events_have_sequence_numbers` | Events can be replayed in order.                  |
| Event append/list         | `test_event_store_append_and_list`  | Event store works.                                |
| Run state save/load       | `test_run_store_save_and_load`      | Runtime state can be restored.                    |
| Provider state save/load  | `test_provider_state_roundtrip`     | Native continuation state survives serialization. |
| Session state transitions | `test_session_status_transitions`   | Session lifecycle is valid.                       |
| Resume from state         | `test_resume_run_from_saved_state`  | Runtime can continue after interruption.          |
| Raw redaction             | `test_raw_payload_redaction`        | Sensitive raw data is handled safely.             |

---

## 6. Workspace functions to validate

For coding-agent workflows.

```python
runtime.workspaces.create(...)
runtime.workspaces.read_file(...)
runtime.workspaces.write_file(...)
runtime.workspaces.run_command(...)
runtime.workspaces.create_patch(...)
runtime.workspaces.snapshot(...)
```

Validate:

| Functionality       | Test name                                     | What it proves                            |
| ------------------- | --------------------------------------------- | ----------------------------------------- |
| Workspace creation  | `test_workspace_create_local`                 | Runtime can create/reference a workspace. |
| File read event     | `test_workspace_file_read_event`              | File reads emit events.                   |
| File write approval | `test_workspace_file_write_requires_approval` | Risky writes can be gated.                |
| Command execution   | `test_workspace_run_command`                  | Commands emit start/completed events.     |
| Command timeout     | `test_workspace_command_timeout`              | Long commands fail safely.                |
| Patch artifact      | `test_workspace_patch_created`                | Diffs become artifacts.                   |
| Snapshot artifact   | `test_workspace_snapshot_created`             | Workspace state can be captured.          |

---

## 7. MCP functions to validate

For first-class MCP support.

```python
runtime.mcp.connect(...)
runtime.mcp.list_tools(...)
runtime.mcp.call_tool(...)
runtime.mcp.disconnect(...)
```

Validate:

| Functionality     | Test name                              | What it proves                       |
| ----------------- | -------------------------------------- | ------------------------------------ |
| MCP stdio connect | `test_mcp_stdio_connect`               | Local MCP server can connect.        |
| MCP HTTP connect  | `test_mcp_http_connect`                | Remote MCP server can connect.       |
| MCP list tools    | `test_mcp_list_tools`                  | Tools are discovered and namespaced. |
| MCP call tool     | `test_mcp_call_tool`                   | MCP tools can be executed.           |
| MCP approval      | `test_mcp_call_requires_approval`      | MCP actions can pause for approval.  |
| MCP auth failure  | `test_mcp_auth_failure`                | Auth errors are typed.               |
| MCP raw events    | `test_mcp_events_preserve_raw_payload` | Native MCP data is preserved.        |

---

## Minimal validation order

I would validate in this order:

```text
1. test_tool_context_injection
2. test_model_stream_text_deltas
3. test_model_emits_tool_call_request
4. test_runtime_run_single_tool_loop
5. test_runtime_run_multi_tool_loop
6. test_runtime_run_tool_loop_with_context_injection
7. test_runtime_run_structured_output
8. test_runtime_run_collects_from_stream
9. test_runtime_run_pauses_for_approval
10. test_resume_run_from_saved_state
```

That sequence proves the new scaffold is not just a provider abstraction. It proves the actual product loop:

```text
call model
detect tool calls
execute tools
inject private context
continue model
validate typed output
emit events
preserve state
pause/resume when needed
```
