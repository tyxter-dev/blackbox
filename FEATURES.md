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
| Workspace agent package contracts | Supported | `WorkspaceAgentSpec`, `WorkspaceAgentRegistry`, `run_workspace_agent(...)` | Portable package metadata for governed agents: connectors, permissions, schedules, skills, publication, serialization, and a thin bridge into the existing runtime loop. |

## Structured Output

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Pydantic structured output | Supported | `runtime.run(output_type=MyBaseModel)` | Uses Pydantic `model_validate_json` on final model text. |
| Dataclass structured output | Supported | `runtime.run(output_type=MyDataclass)` | Parses final JSON and constructs the dataclass. |
| String output | Supported | `runtime.run(output_type=str)` | Explicit or implicit default. |
| Fail-fast validation errors | Supported | `OutputValidationError` | Carries `raw_text` and the original validation/JSON error when available. |
| Provider-native structured output | Supported | `OutputSpec.strategy="provider_native"` | Runtime builds JSON Schema contracts and wires them into OpenAI Responses `text.format`, Gemini `response_json_schema`, and Anthropic `output_config.format` for supported Claude model versions; unsupported providers/models raise unless an explicit fallback is selected. |
| Finalizer tool structured output | Supported | `OutputSpec.strategy="finalizer_tool"` | Runtime injects a hidden `submit_final_output` tool, terminates when the model calls it, and validates the tool arguments as final output. |
| Raw JSON Schema post-hoc output | Supported | `OutputSpec(schema={...}, strategy="posthoc_parse")` | Dict JSON Schemas validate common JSON Schema constraints after generation, including type, required fields, additional properties, arrays, enums, constants, `anyOf`, and `oneOf`. |
| Structured output retry/repair | Supported | `OutputSpec.strategy="posthoc_parse_with_retry"` | On validation failure, the runtime feeds a repair prompt back to the model up to `max_validation_retries`. |

## Local Tools

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Local tool registration | Supported | `runtime.tools.register(...)`, `ToolRegistry.register(...)` | Registers normal Python callables as named tools. |
| Dynamic tool sessions | Supported | `runtime.tools.session()` | Creates an isolated tool registry seeded from global tools so one run can attach ad hoc tools without leaking them into later runs. |
| Tool lookup/listing | Supported | `runtime.tools.get(...)`, `runtime.tools.all_tools()` | Returns `ToolDefinition` records. |
| Provider-neutral tool schema export | Supported | `runtime.tools.to_provider_tools()` | Exports registered tools as function schemas for model providers. |
| Manual JSON schema override | Supported | `register(parameters={...})` | Caller may provide the model-visible schema explicitly. |
| Tool metadata | Supported | `category`, `tags`, `risk`, `scopes`, `latency`, `cost`, `side_effects`, `examples`, `negative_examples`, `metadata` | Stored on `ToolDefinition`; useful for catalog/search/routing layers. |
| Blocking tool offload | Supported | `register(blocking=True)` | Blocking tools are executed off the event loop. |
| Async tool execution | Supported | Async callable tools | Async functions are awaited directly. |
| Tool timeouts | Supported | `ToolRuntime(timeout_seconds=...)` | Long-running tools fail with typed tool errors. |
| Tool concurrency cap | Supported | `ToolRuntime(max_concurrency=...)` | Limits concurrent tool execution. |
| High-level tool controls | Supported | `runtime.run(tool_timeout=..., tool_max_concurrent=...)` | Applies timeout and concurrency limits to tool execution in the blackbox loop. |
| Multiple tool calls in one turn | Supported | `AgentLoop` | Dispatches all tool calls requested by a model turn before continuing. |
| Tool result coercion | Supported | Return `ToolResult`, `str`, `dict`, or other object | Non-`ToolResult` returns are converted into `ToolResult`. |
| Tool payload separation | Supported | `ToolResult(content=..., payload=...)` | `content` goes back to the model; `payload` is collected for application use in `AgentResult.payloads`. |
| Context injection | Supported | `tool_execution_context={...}` / `ToolRuntime(context=...)` | Private runtime values are injected by parameter name. |
| Context schema privacy | Supported | Injected context parameters are not added to provider tool schemas. |
| Tool catalog search | Supported | `ToolCatalog` | Simple relevance-scored catalog/search layer for registered/cataloged tools. |
| Runtime toolsets and dynamic loading | Supported | `Toolset`, `ToolBudget`, `runtime.run(toolsets=[...], tool_selection="dynamic")` | Large catalogs can expose `search_tools`/`load_tools`, defer concrete tools until loaded, enforce visible/call/parallel budgets, and emit tool-choice telemetry. |
| Workspace tool backend | Supported | `runtime.tools.register_workspace(...)` | Compatibility bridge for exposing provider-backed workspace operations as run-scoped local tools. |
| Namespaced `ToolRef` IDs | Not supported yet | Planned for MCP | Current high-level API references tools by simple name. |

## Hosted Tools

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Typed hosted tool specs | Supported | `WebSearch`, `FileSearch`, `CodeInterpreter`, `RemoteMCP`, `ToolSearch`, `ImageGeneration`, `TextEditor`, `Memory`, `HostedToolRaw` | Provider-managed tools are passed via `hosted_tools` and remain distinct from local function tools. |
| OpenAI hosted tool mapping | Supported | `runtime.run(..., hosted_tools=[...])` | Maps web search, file search, code interpreter, remote MCP, and raw hosted payloads into Responses `tools`; file search result inclusion is wired through `include`. |
| OpenAI hosted tool events | Supported | `RunItem(type="hosted_tool_call")` | Known hosted output items are normalized into typed run items while preserving provider raw payloads. |
| Hosted tool result metadata | Supported | `result.metadata["hosted_tools"]` | Summarizes observed hosted tool calls by type, item ID, status, query/call ID, and result count where available. |
| Provider-native remote MCP | Supported | `RemoteMCP` | OpenAI maps to `type="mcp"` tools; Anthropic maps to `mcp_servers` plus `mcp_toolset` with the current MCP client beta. |
| MCP result metadata | Supported | `result.metadata["mcp"]` | Summarizes MCP tool lists, calls, approvals, and OpenAI tool-list context item IDs that callers can retain for lower-latency continuation. |
| Gemini hosted web search | Supported | `WebSearch` | Maps to GenerateContent `google_search`; unsupported hosted tool specs raise typed unsupported-feature errors. |
| Raw hosted tool passthrough | Supported | `HostedToolRaw` | Escape hatch for provider-native tool payloads where the runtime has no typed wrapper yet. |

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
| Session store protocol | Supported | `SessionStore` | Dedicated agent-session lifecycle store for session snapshots, event cursors, invocations, approvals, workspace/MCP state, and artifact refs. |
| In-memory session store | Supported | `InMemorySessionStore` | Process-local session snapshots and replay ledger. |
| Provider-native continuation state | Supported | `ProviderState` | Preserves provider continuation IDs and native state outside chat history. |
| OpenAI previous response continuation | Supported | `ProviderState.previous_response_id` | OpenAI Responses adapter round-trips `previous_response_id`. |
| Provider tool/source state preservation | Supported | `ProviderState.tool_state`, `ProviderState.reasoning_state` | Adapters retain native tool IDs, hosted/MCP IDs, output item IDs, source references, file handles, and reasoning signatures where providers expose them. |
| Provider-native request controls | Supported | `ModelRequestControls` / `TurnRequest.controls` | Common controls such as instructions, sampling, output token caps, tool choice, parallel tool calls, reasoning effort, verbosity, tool search, compaction/truncation, background mode, store, and include are mapped by adapters where native support exists; `extra` remains the escape hatch. |
| Model usage and cost accounting | Supported | `ModelUsage`, `ModelCatalog`, `ModelPricing` | Provider adapters normalize usage, split cache read/cache creation tokens where available, count tool calls, preserve provider usage details, and applications can register current pricing to add cost estimates to result metadata. |
| Native provider cache controls | Supported | `ModelCacheControl`, `cache=...` | OpenAI/xAI map prompt cache keys/retention, Anthropic maps ephemeral cache controls, and Gemini consumes `cached_content` names. Result metadata reports cache hit metrics where provider usage exposes them. |
| Provider cache registry | Supported | `ProviderCacheRuntime`, `ProviderCacheStore` | In-memory and SQLite provider cache stores track cache keys, provider cache IDs, TTL/expiry, hit/miss counts, and token totals; runtime APIs support invalidation and expired-entry eviction. |
| Provider-side cache lifecycle | Partial | `runtime.caches.create(...)`, `runtime.caches.invalidate(..., delete_provider_cache=True)` | Gemini context cache creation/deletion is wired through `client.caches.create/delete`; providers without native create/delete APIs still get local registry invalidation. |
| Raw provider payload preservation | Supported | `AgentEvent.raw`, `RawEnvelope` | Provider adapters can keep original SDK payloads; `RawEnvelope` supports sensitivity tagging/redaction. |
| Resume run from persisted state | Supported | `provider_state=...`, `RunStore` | `RunState` can be saved, reloaded in a fresh runtime, and passed back into `AgentRuntime.run`. |
| JSONL/SQLite stores | Supported | `JSONLEventStore`, `JSONLSessionStore`, `SQLiteRunStore`, `SQLiteSessionStore` | JSONL event/session logs plus SQLite run/session persistence are implemented and covered by tests. |

## Granular Model Capabilities

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Model capability profile | Supported | `runtime.models.capabilities(...)` | Returns hosted tool, output strategy, control, and state-mode support. |
| Hosted-tool capability validation | Supported | Runtime validation | Explicit unsupported typed hosted tools raise before provider calls. |
| Output strategy fallback resolution | Supported | `OutputSpec.fallback` | Runtime chooses a supported fallback strategy or raises. |
| Control capability validation | Supported | `ModelRequestControls` | Explicit unsupported controls raise before provider calls. |
| State mode validation | Supported | `ModelRequestControls.state_mode` | Explicit unsupported state modes raise before provider calls. |
| Compatibility profile derivation | Supported | `get_model_capability_profile(...)` | Providers without `capability_profile(...)` still get a conservative profile derived from flat `ModelCapabilities`. |
| OpenAI tool-search control | Supported | `ToolSearchControl` / `ToolSearch` | OpenAI can request provider tool search directly or through hosted namespace specs without duplicate tool-search entries. |
| Provider-native compaction control | Supported | `CompactionControl(strategy="auto" | "disabled")` | OpenAI maps to Responses `truncation`; supported Anthropic Claude 4.6 models map to `context_management.edits`; aggressive/custom compaction raises until a full compaction workflow exists. |
| Modalities control | Contract only | `ModelRequestControls.modalities` | Explicit typed surface exists and unsupported providers reject it; no adapter maps it yet. |

## Provider Runtime

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Provider registry | Supported | `ProviderRegistry` | Registers/resolves model and agent providers. |
| Provider reference parsing | Supported | `ProviderRef.parse(...)` | Supports `provider:model` as canonical format; slash remains compatible. |
| Model provider protocol | Supported | `ModelProvider` | Providers implement `stream_turn(TurnRequest)`. |
| Agent provider protocol | Supported | `AgentProvider` | Providers implement agent/session lifecycle methods. |
| Chat compatibility facade | Supported | `runtime.chat`, `ChatMessage` | Explicitly projects chat-shaped messages into model runtime inputs for migration paths without making chat the internal source of truth. |
| Capability flags | Supported | `ModelCapabilities`, `AgentCapabilities` | Providers advertise supported behavior; tests assert honesty. |
| Granular model capability profile | Supported | `ModelCapabilityProfile` | Providers can expose model-specific support for hosted tools, output strategies, controls, state modes, and simple cross-feature constraints without changing the base `ModelProvider` protocol. |
| Direct model run | Supported | `runtime.models.run(...)` | Collects text and provider state from a model turn. |
| Direct model stream | Supported | `runtime.models.stream(...)` | Streams provider-normalized events. |
| Echo model provider | Supported | `EchoModelProvider` | Dependency-free provider for tests/examples. |
| OpenAI Responses model provider | Supported | `OpenAIResponsesProvider` | Native Responses streaming adapter with typed event mapping and raw payload preservation. |
| xAI Responses model provider | Supported | `XAIResponsesProvider` | OpenAI-compatible Responses adapter pointed at xAI, with typed event mapping, raw payload preservation, and conservative capability flags for unsupported hosted search/MCP surfaces. |
| Anthropic Messages model provider | Supported | `AnthropicMessagesProvider` | Native Messages streaming adapter with typed event mapping, tool conversion, reasoning deltas, model-gated structured output/compaction, provider state, and raw payload preservation. |
| Gemini GenerateContent model provider | Supported | `GeminiGenerateContentProvider` | Native GenerateContent streaming adapter with text, reasoning, function-call mapping, function-response continuation, provider state, and Gemini-3-gated structured-output-with-tools support. |

## Agent Sessions

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Local agent provider | Supported | `LocalAgentProvider` | Session-shaped wrapper around the shared `AgentLoop`. |
| Create agent | Supported | `runtime.agents.create_agent(...)` | Creates an `AgentRef` for local providers. |
| Create/start session | Supported | `runtime.agents.create_session(...)` | Produces first-class `AgentSession` objects. |
| Stream session events | Supported | `runtime.agents.stream(...)` | Emits normalized model/tool/session events. |
| Durable session persistence | Supported | `AgentRuntime(session_store=...)` | Session creation, streamed events, cursor mappings, invocation state, approval state, provider state, workspace/MCP state, and artifact refs are stored in `SessionStore`. |
| Durable session replay | Supported | `runtime.agents.replay(...)`, `runtime.agents.stream(..., after_event_id=...)` | Runtime event IDs are the public replay cursor; stored terminal sessions replay without contacting the provider. |
| Provider-native live resume | Supported where advertised | `runtime.agents.load_session(...)`, `runtime.agents.resume_session(...)` | OpenAI Agents and Claude Code session refs preserve provider session IDs and receive provider-native cursors on live resume. Providers without `supports_resume` replay stored history but do not claim live reconnect. |
| Send message to session | Supported | `runtime.agents.send_message(..., idempotency_key=...)` | Follow-up status behavior is defined; idempotency keys return the original invocation ref. |
| Cancel session | Supported | `runtime.agents.cancel(...)` | Cancellation is honored between turns for local sessions. |
| List session artifacts | Supported | `runtime.agents.list_artifacts(...)` | Local provider returns an `ArtifactPage`; no artifact-producing workspace flow yet. |
| Session approval pause/resume/deny | Supported | `runtime.agents.approve(...)` | Local sessions can wait for approval and continue or skip denied tools. |
| OpenAI Agents SDK provider | Supported | `OpenAICloudAgentProvider` | Supports injected `client=...` implementations and the optional production `openai-agents` wrapper for Agents SDK sessions, streaming events, HITL approval resume, artifacts, cancellation, MCP-capable agent definitions, and continuation metadata. |
| Claude Code Agent SDK provider | Supported | `ClaudeCodeAgentProvider` | Supports injected `client=...` implementations and the optional production `claude-agent-sdk` wrapper for sessions, streaming events, workspace/tool events, approvals, artifacts, cancellation, and resume metadata. |
| Cloud agent providers | Partial | `OpenAICloudAgentProvider`, `ClaudeCodeAgentProvider`, `VertexAIAgentEngineProvider` | OpenAI Agents SDK and Claude Code are implemented; Vertex Agent Engine remains an honest stub. |

## Artifacts, Workspaces, MCP, and Observability

| Feature | Status | Public surface | Notes |
|---|---|---|---|
| Artifact data contracts | Supported | `Artifact`, `ArtifactRef`, `ArtifactPage` | Typed artifact models exist. |
| Workspace runtime facade | Supported | `runtime.workspaces.open(...)` | Opens and tracks local, git, cloud, and registered sandbox/Docker providers; `runtime.agents.run(..., workspace=...)` receives a `WorkspaceRef` instead of raw tools. |
| Local workspace runtime | Supported | `WorkspaceRuntime` / `LocalWorkspaceProvider` | Local workspaces support file read/write/delete, patch artifacts, command execution, snapshots, policy gates, and canonical events. |
| Workspace data contracts | Supported | `workspaces.spec`, `workspaces.changes` | Workspace refs, mounts, file changes, patch artifacts, commands, and command results exist. |
| Workspace agent package contracts | Supported | `workspace_agents` | Governed workspace-agent packages model connector auth modes, tool permissions, schedules, skills, publication metadata, registry protocols, and serialization without downstream UI/scheduler/storage assumptions. |
| MCP server spec | Supported | `MCPServerSpec` | Server configuration model exists for `stdio`, `http`, `sse`, and `streamable_http`, including auth, protocol negotiation, allow/deny filters, timeout, cache, output-limit, and redaction controls. |
| Local MCP dispatch connector | Supported | `MCPConnector` | Registers in-process MCP tools, starts managed stdio/HTTP transports, lists namespaced `mcp:<server>.<tool>` refs, gates calls through policy, normalizes MCP results, caches discovery, and emits MCP lifecycle/cache/call events. |
| MCP runtime tool bridge | Supported | `MCPConnector.register_runtime_tools(...)` | Exposes discovered MCP tools to a `ToolRegistry` while preserving MCP payload metadata and routing calls through the connector. |
| MCP toolset routing | Supported | `MCPToolset` / `runtime.run(..., toolsets=[...])` | Routes MCP servers to local dispatch or provider-native `RemoteMCP` based on mode, provider capability, transport, URL locality, auth, and policy. |
| Observability sink protocol | Supported | `observability.sinks` | Event sink abstractions exist and can be wrapped by redaction before telemetry export. |
| Event trace context | Supported | `AgentEvent.trace_id`, `span_id`, `parent_span_id`, `span_kind` | Runtime stamps every streamed event with workflow trace context while preserving provider correlation IDs. |
| Workflow trace spans | Supported | `result.metadata["trace"]`, `trace_from_events(...)` | Runtime reconstructs workflow-rooted span trees for model, tool, hosted tool, MCP, workspace, approval, artifact, retry, handoff, guardrail, and eval events. |
| OpenTelemetry trace export | Supported | `OpenTelemetryTraceExporter` | Optional `otel` extra exports reconstructed traces through the installed OpenTelemetry SDK/exporter pipeline. |
| Replay and run diffing | Supported | `replay_run(...)`, `diff_runs(...)`, `diff_traces(...)` | Stored events can be replayed into timelines and compared by span topology, status, duration, and cost. |
| Evaluator hooks | Supported | `evaluate_trace(...)` | Replay-time or post-run evaluators can emit `eval.started` / `eval.completed` / `eval.failed` events. |

## Explicitly Not Supported Yet

- Vertex Agent Engine execution.
- Provider-breadth routing through LiteLLM.
- Live resume for local `AgentLoop` sessions after process restart.
