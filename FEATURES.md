# Feature Catalog

This catalog lists the features currently supported by `agent_runtime`.
Use it as a product/runtime inventory, not as a roadmap.

Status legend:

- **Supported**: implemented and covered by current tests or examples.
- **Partial**: API or adapter exists, but behavior is incomplete or provider-limited.
- **Contract only**: data model or interface exists, but no full runtime behavior yet.
- **Not supported yet**: intentionally not part of the current implementation.

## High-Level Runtime

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Complete blackbox agent loop | Supported | `await runtime.run(...)` | Owns model turn, tool-call detection, local tool dispatch, tool-result continuation, final text collection, and result creation. |
| Streaming blackbox loop | Supported | `async for event in runtime.stream(...)` | Same loop as `runtime.run`, exposed as canonical events. |
| Typed agent result | Supported | `AgentResult[T]` | Carries `output`, `text`, `events`, `items`, `artifacts`, `payloads`, `provider_state`, and `metadata`. |
| Text-only runs | Supported | `runtime.run(output_type=None)` | Returns final text as `AgentResult.output` and `AgentResult.text`. |
| Max-iteration guard | Supported | `runtime.run(max_iterations=...)` | Prevents unbounded model/tool loops and emits a terminal failure event. |
| Mock tool execution | Supported | `runtime.run(mock_tools=True)` / `runtime.tools.call(..., mock=True)` | Short-circuits real tool side effects for tests and demos. |

## Structured Output

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Pydantic structured output | Supported | `runtime.run(output_type=MyBaseModel)` | Uses Pydantic `model_validate_json` on final model text. |
| Dataclass structured output | Supported | `runtime.run(output_type=MyDataclass)` | Parses final JSON and constructs the dataclass. |
| String output | Supported | `runtime.run(output_type=str)` | Explicit or implicit default. |
| Fail-fast validation errors | Supported | `OutputValidationError` | Carries `raw_text` and the original validation/JSON error when available. |
| Provider-native structured output | Contract only | `OutputSpec.strategy="provider_native"` | Strategy is reserved; full provider-native wiring is not implemented yet. |
| Structured output retry/repair | Supported | `OutputSpec.strategy="posthoc_parse_with_retry"` | On validation failure, the runtime feeds a repair prompt back to the model up to `max_validation_retries`. |

## Local Tools

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Local tool registration | Supported | `runtime.tools.register(...)`, `ToolRegistry.register(...)` | Registers normal Python callables as named tools. |
| Tool lookup/listing | Supported | `runtime.tools.get(...)`, `runtime.tools.all_tools()` | Returns `ToolDefinition` records. |
| Provider-neutral tool schema export | Supported | `runtime.tools.to_provider_tools()` | Exports registered tools as function schemas for model providers. |
| Manual JSON schema override | Supported | `register(parameters={...})` | Caller may provide the model-visible schema explicitly. |
| Tool metadata | Supported | `category`, `tags`, `metadata` | Stored on `ToolDefinition`; useful for catalog/search/routing layers. |
| Blocking tool offload | Supported | `register(blocking=True)` | Blocking tools are executed off the event loop. |
| Async tool execution | Supported | Async callable tools | Async functions are awaited directly. |
| Tool timeouts | Supported | `ToolRuntime(timeout_seconds=...)` | Long-running tools fail with typed tool errors. |
| Tool concurrency cap | Supported | `ToolRuntime(max_concurrency=...)` | Limits concurrent tool execution. |
| Multiple tool calls in one turn | Supported | `AgentLoop` | Dispatches all tool calls requested by a model turn before continuing. |
| Tool result coercion | Supported | Return `ToolResult`, `str`, `dict`, or other object | Non-`ToolResult` returns are converted into `ToolResult`. |
| Tool payload separation | Supported | `ToolResult(content=..., payload=...)` | `content` goes back to the model; `payload` is collected for application use in `AgentResult.payloads`. |
| Context injection | Supported | `tool_execution_context={...}` / `ToolRuntime(context=...)` | Private runtime values are injected by parameter name. |
| Context schema privacy | Supported | Injected context parameters are not added to provider tool schemas. |
| Tool catalog search | Supported | `ToolCatalog` | Simple relevance-scored catalog/search layer for registered/cataloged tools. |
| Workspace tool backend | Supported | `runtime.tools.register_workspace(...)` | Registers local workspace operations as normal tools for the high-level agent loop. |
| Namespaced `ToolRef` IDs | Not supported yet | Planned for MCP | Current high-level API references tools by simple name. |

## Policy, Approvals, and Safety

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Allow-all default policy | Supported | Default runtime behavior | Tool calls run unless a policy/approval layer blocks them. |
| Policy allow/deny gate | Supported | `policy=Policy` | `Policy.check(...)` can allow or deny before a tool call. |
| Approval-required gate | Supported | `PolicyDecision.require_approval(...)` / `approval_policy=...` | Runtime emits `approval.requested` and pauses before executing the tool. |
| Approval resume | Supported | `runtime.agents.approve(...)` for sessions / `AgentLoop.approve(...)` internally | Approval can resume a waiting local agent session. |
| Approval denial | Supported | `ApprovalDecision.deny(...)` | Denied calls become failed function-result items instead of executing. |
| Typed approval errors | Supported | `ApprovalError` | Raised for invalid approval IDs or approval misuse. |

## Events, State, and Persistence

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Canonical event taxonomy | Supported | `AgentEvent`, `EventTypes` | Covers run/session, model, tools, MCP, cloud agents, workspace, approvals, artifacts, and eval domains. |
| Run IDs | Supported | `AgentEvent.run_id` | `runtime.run/.stream` stamps each event with a per-run UUID. |
| Monotonic event sequence | Supported | `AgentEvent.sequence` | Events from a run can be replayed in order. |
| Event store protocol | Supported | `EventStore` | Runtime writes streamed high-level events into the configured store. |
| In-memory event store | Supported | `InMemoryEventStore` | Append and ordered list operations are implemented. |
| Run store protocol | Supported | `RunStore` | Interface exists for saving/loading `RunState`. |
| In-memory run store | Supported | `InMemoryRunStore` | Save/load/all-runs operations are implemented. |
| Provider-native continuation state | Supported | `ProviderState` | Preserves provider continuation IDs and native state outside chat history. |
| OpenAI previous response continuation | Supported | `ProviderState.previous_response_id` | OpenAI Responses adapter round-trips `previous_response_id`. |
| Native provider caching | Partial | `ProviderState` only | The library preserves provider state for continuation. It does not yet expose provider cache controls, cache eviction, or persistent cache backends. |
| Raw provider payload preservation | Supported | `AgentEvent.raw`, `RawEnvelope` | Provider adapters can keep original SDK payloads; `RawEnvelope` supports sensitivity tagging/redaction. |
| Resume run from persisted state | Supported | `provider_state=...`, `RunStore` | `RunState` can be saved, reloaded in a fresh runtime, and passed back into `AgentRuntime.run`. |
| JSONL/SQLite stores | Supported | `JSONLEventStore`, `SQLiteRunStore` | JSONL event logs and SQLite run-state persistence are implemented and covered by tests. |

## Provider Runtime

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Provider registry | Supported | `ProviderRegistry` | Registers/resolves model and agent providers. |
| Provider reference parsing | Supported | `ProviderRef.parse(...)` | Supports `provider:model` as canonical format; slash remains compatible. |
| Model provider protocol | Supported | `ModelProvider` | Providers implement `stream_turn(TurnRequest)`. |
| Agent provider protocol | Supported | `AgentProvider` | Providers implement agent/session lifecycle methods. |
| Capability flags | Supported | `ModelCapabilities`, `AgentCapabilities` | Providers advertise supported behavior; tests assert honesty. |
| Direct model run | Supported | `runtime.models.run(...)` | Collects text and provider state from a model turn. |
| Direct model stream | Supported | `runtime.models.stream(...)` | Streams provider-normalized events. |
| Echo model provider | Supported | `EchoModelProvider` | Dependency-free provider for tests/examples. |
| OpenAI Responses model provider | Supported | `OpenAIResponsesProvider` | Native Responses streaming adapter with typed event mapping and raw payload preservation. |
| xAI Responses model provider | Supported | `XAIResponsesProvider` | OpenAI-compatible Responses adapter pointed at xAI, with typed event mapping, raw payload preservation, and conservative capability flags for unsupported hosted search/MCP surfaces. |
| Anthropic Messages model provider | Supported | `AnthropicMessagesProvider` | Native Messages streaming adapter with typed event mapping, tool conversion, reasoning deltas, provider state, and raw payload preservation. |
| Gemini GenerateContent model provider | Supported | `GeminiGenerateContentProvider` | Native GenerateContent streaming adapter with text, reasoning, function-call mapping, function-response continuation, and provider state. |

## Agent Sessions

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Local agent provider | Supported | `LocalAgentProvider` | Session-shaped wrapper around the shared `AgentLoop`. |
| Create agent | Supported | `runtime.agents.create_agent(...)` | Creates an `AgentRef` for local providers. |
| Create/start session | Supported | `runtime.agents.create_session(...)` | Produces first-class `AgentSession` objects. |
| Stream session events | Supported | `runtime.agents.stream(...)` | Emits normalized model/tool/session events. |
| Send message to session | Partial | `runtime.agents.send_message(...)` | Invocation ref is wired; behavior is not deeply covered yet. |
| Cancel session | Supported | `runtime.agents.cancel(...)` | Cancellation is honored between turns for local sessions. |
| List session artifacts | Supported | `runtime.agents.list_artifacts(...)` | Local provider returns an `ArtifactPage`; no artifact-producing workspace flow yet. |
| Session approval pause/resume/deny | Supported | `runtime.agents.approve(...)` | Local sessions can wait for approval and continue or skip denied tools. |
| Cloud agent providers | Partial | `OpenAICloudAgentProvider`, `ClaudeCodeAgentProvider`, `VertexAIAgentEngineProvider` | Provider shells/stubs exist; full cloud execution is not implemented. |

## Artifacts, Workspaces, MCP, and Observability

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Artifact data contracts | Supported | `Artifact`, `ArtifactRef`, `ArtifactPage` | Typed artifact models exist. |
| Local workspace runtime | Supported | `WorkspaceRuntime` | Local workspaces support file read/write/delete, patch artifacts, command execution, snapshots, policy gates, and canonical events. |
| Workspace data contracts | Supported | `workspaces.spec`, `workspaces.changes` | Workspace refs, mounts, file changes, patch artifacts, commands, and command results exist. |
| MCP server spec | Supported | `MCPServerSpec` | Server configuration model exists for `stdio`, `http`, `sse`, and `streamable_http`; process/client management remains planned. |
| Local MCP dispatch connector | Supported | `MCPConnector` | Registers local MCP tools, lists namespaced `mcp:<server>.<tool>` refs, gates calls through policy, and emits MCP events. |
| Observability sink protocol | Partial | `observability.sinks` | Event sink abstractions exist; full tracing/export integrations are not implemented. |
| Trace data contracts | Partial | `observability.traces` | Trace/span models exist; no full tracing backend yet. |

## Explicitly Not Supported Yet

- OpenAI cloud/Codex-style agent execution.
- Claude Code or Vertex Agent Engine execution.
- MCP stdio/HTTP transport process management or provider-native remote MCP wiring.
- Provider cache controls beyond preserving native continuation state.
- Provider-breadth routing through LiteLLM.
