# Cloud Agent and MCP Transport Implementation Spec

Status: partially implemented.

Source of truth: `FEATURES.md` lists the public feature status and
`tests/VALIDATION.md` lists the test coverage. This spec is retained as the
forward plan for remaining cloud-agent work and MCP hardening.

Implemented since this spec was drafted:

- `MCPConnector` can start managed stdio/HTTP/SSE/Streamable HTTP transports
  through injected transport clients, initialize/list/call tools, cache and
  refresh tool definitions, stop transports, and bridge discovered MCP tools
  into `ToolRegistry`.
- Provider-native remote MCP request mapping exists for OpenAI and Anthropic
  through `RemoteMCP`.
- `ClaudeCodeAgentProvider` has a client-backed lifecycle covered by fake
  client tests.
- `ClaudeCodeAgentProvider` can lazy-load the optional `claude-agent-sdk`
  package and run a real SDK-backed lifecycle without an injected client.
- `OpenAICloudAgentProvider` can lazy-load the optional `openai-agents`
  package and supervise OpenAI Agents SDK runs through the `AgentProvider`
  lifecycle.

Remaining:

- Vertex Agent Engine execution.
- Broader MCP OAuth/token-provider flows and production transport coverage.

Scope: implement the two highest-leverage gaps from the adversarial review:

1. Make one cloud/coding-agent `AgentProvider` real.
2. Implement real MCP transport management for local/runtime MCP servers.

This spec supersedes the broad critique for implementation planning. The repo
already has typed hosted tools, provider-native remote MCP payload mapping, MCP
event normalization, local workspace primitives, persistence, and local
approval pauses. The remaining core failure is at the external execution
boundary: cloud agent sessions are still stubs, and the local MCP connector does
not yet manage protocol transports.

References:

- [OpenAI Codex cloud](https://platform.openai.com/docs/codex/overview)
  describes cloud coding tasks that run in sandboxed environments, can
  read/write/run code, and continue in the background.
- [OpenAI tools](https://platform.openai.com/docs/guides/tools?api-mode=responses)
  list remote MCP, shell, computer use, apply patch, hosted tools, and code
  interpreter as normal tool surfaces.
- [OpenAI remote MCP](https://platform.openai.com/docs/guides/tools-remote-mcp?lang=python)
  requires server configuration such as `server_url`, supports approval flows,
  and works with Streamable HTTP or HTTP/SSE transports.
- [MCP transports](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)
  define stdio and Streamable HTTP as standard transports, with legacy HTTP/SSE
  compatibility.
- [MCP authorization](https://modelcontextprotocol.io/specification/2025-03-26/basic/authorization)
  defines HTTP transport authorization expectations.
- [OpenAI Agents SDK tracing](https://openai.github.io/openai-agents-python/tracing/)
  treats nested agent, model, tool, guardrail, and handoff spans as normal
  production observability.

## Why These Two

### 1. Cloud/coding-agent provider

This was the top issue because `AgentProvider` is one of the library's central
abstractions. `ClaudeCodeAgentProvider` and `OpenAICloudAgentProvider` now have
concrete SDK-backed lifecycles covered by deterministic fake-SDK tests. Vertex
Agent Engine remains an honest stub until its public API surface is stable
enough to implement without guesswork.

### 2. MCP transport management

This was the second issue because MCP is the most important cross-provider
tool integration layer. The local-dispatch-only critique is now obsolete:
`MCPConnector` supports managed transport clients in tests, initializes/list
tools/calls tools through those transports, caches/refreshes tool definitions,
and stops transports cleanly. Remaining work is production hardening around
real transport clients, OAuth/token-provider flows, and broader integration
coverage.

## Non-Goals

- Do not build all cloud providers at once.
- Do not flatten cloud agents into local tools or model turns.
- Do not replace provider-native `RemoteMCP` hosted-tool mapping.
- Do not implement computer-use/browser/desktop automation in this spec.
- Do not add a general memory system or evaluation backend here.
- Do not make network-dependent tests mandatory in the offline test suite.

## Feature 1: Real Cloud/Coding-Agent Provider

### Target Provider

Phase 1 target:

```text
src/agent_runtime/agents/claude_code.py
```

`ClaudeCodeAgentProvider` was the first concrete non-local `AgentProvider`.
`OpenAICloudAgentProvider` now targets the OpenAI Agents SDK. `VertexAIAgentEngineProvider`
remains an honest stub until its public API surface is stable enough to
implement without guesswork.

### Required Behavior

The provider must implement:

- `create_agent(spec)` - register a reusable agent definition locally or with
  the SDK and return an `AgentRef`.
- `start_session(agent, task)` - submit work and return an `AgentSession`
  containing provider-native session/task IDs in metadata.
- `stream_events(session, after_event_id=None)` - stream normalized
  `AgentEvent`s while preserving raw provider payloads.
- `send_message(session, message)` - continue an existing session and return an
  `InvocationRef`.
- `approve(approval_id, decision)` - resume or deny provider/tool approval
  pauses when the underlying SDK exposes them.
- `cancel(session)` - request cancellation and emit/observe terminal
  cancellation.
- `list_artifacts(session, type=None, after=None, limit=100)` - list files,
  patches, logs, or provider artifact references available for the session.
- Resume - accept an `AgentSession` or `SessionRef` with enough metadata to
  reconnect after process restart.

### Capability Rules

`ClaudeCodeAgentProvider.capabilities()` may advertise a flag only after the
corresponding method has deterministic tests:

```python
AgentCapabilities(
    supports_sessions=True,
    supports_streaming_events=True,
    supports_artifacts=True,
    supports_workspace=True,
    supports_approvals=<true only if wired>,
    supports_mcp=<true only if SDK MCP is wired>,
    supports_cancellation=True,
    supports_resume=True,
)
```

If the SDK does not support a capability, the provider must keep the flag false
and keep raising `UnsupportedFeatureError` for that operation.

### Session Metadata Contract

Cloud sessions need stable provider metadata. Store these fields in
`AgentSession.metadata` and `SessionRef.metadata`:

```python
{
    "provider_session_id": "...",
    "provider_invocation_id": "...",
    "workspace": {
        "kind": "local" | "cloud" | "git",
        "root": "...",
        "provider_workspace_id": "...",
    },
    "resume_token": "...",
    "last_event_id": "...",
    "raw": {...},
}
```

Rules:

- `AgentSession.id` stays runtime-owned and stable.
- Provider-native IDs are metadata, never replacement runtime IDs.
- Raw provider state must be serializable or wrapped in a redaction-aware
  envelope before persistence.

### Event Mapping

Map provider events to existing canonical events first:

| Provider meaning | Canonical event |
| --- | --- |
| session created/started | `session.created`, `session.started` |
| status/log/checkpoint | `cloud_agent.status.changed`, `cloud_agent.log`, `cloud_agent.checkpoint.created` |
| text/model delta | `model.text.delta`, `model.reasoning.delta` when available |
| tool request/result | `tool.call.requested`, `tool.call.completed`, `tool.call.failed` |
| MCP list/call/approval | `mcp.list_tools.completed`, `mcp.call.started`, `mcp.call.completed`, `mcp.approval.required` |
| file changed/patch | `workspace.file.changed`, `workspace.patch.created` |
| artifact | `artifact.created`, `artifact.updated` |
| approval pause | `approval.requested` |
| terminal states | `session.completed`, `session.failed`, `session.cancelled` |

Every mapped event must preserve the raw SDK object or decoded payload on
`event.raw` unless redaction policy disallows storage.

### Artifacts

Provider artifacts normalize to `Artifact` where possible:

- generated patch or diff -> `type="patch"`
- changed file reference -> `type="file"`
- command/test log -> `type="log"`
- provider-only reference -> `type="provider_ref"`

If the provider exposes only a URL or opaque ID, do not download it implicitly.
Return an `ArtifactRef` or metadata that lets the caller request export later.

### Approvals

Approval behavior must use the existing `ApprovalRequest` and
`ApprovalDecision` types:

1. Provider emits an approval-required signal.
2. Adapter emits `approval.requested` with `approval_id`, action, arguments,
   and provider metadata.
3. `stream_events` transitions the session to `waiting` if the provider pauses.
4. `approve(approval_id, decision)` forwards the approval or denial.
5. Adapter emits `approval.approved` or `approval.denied`.
6. The provider resumes, skips, or fails according to native semantics.

If approval resume is not supported, emit the provider-specific raw event and
raise `UnsupportedFeatureError` from `approve`.

### Implementation Shape

Add a small adapter boundary instead of binding tests directly to a third-party
SDK:

```text
src/agent_runtime/agents/claude_code.py
src/agent_runtime/agents/_cloud_sessions.py
tests/fixtures/fake_claude_code_client.py
```

Internal client protocol:

```python
class ClaudeCodeClient(Protocol):
    async def create_agent(...): ...
    async def start_session(...): ...
    def stream_events(...): ...
    async def send_message(...): ...
    async def approve(...): ...
    async def cancel(...): ...
    async def list_artifacts(...): ...
```

The production adapter lazy-imports the real SDK. Tests use the fake client.
This keeps the offline suite deterministic and avoids locking the public API to
one SDK version before implementation validates it.

### Tests

Contract tests:

- `ClaudeCodeAgentProvider` advertises only implemented capabilities.
- Unsupported operations still raise typed errors when the fake client disables
  a capability.

Unit/runtime tests:

- create agent returns `AgentRef` with provider metadata.
- start session returns running `AgentSession` with provider session ID.
- stream maps status, log, file, patch, artifact, approval, and terminal events.
- `after_event_id` resumes from the fake event cursor.
- `send_message` returns `InvocationRef` and streams follow-up events.
- `cancel` emits or observes `session.cancelled`.
- `list_artifacts` paginates and filters by type.
- approval pause/resume and denial use `ApprovalDecision`.
- provider raw payloads are preserved and redaction-compatible.

Integration tests:

- Add `tests/integration/agents/test_claude_code_smoke.py`.
- Mark with `integration_cloud_agent`.
- Skip without the provider credentials or SDK-specific environment variables.

## Feature 2: MCP Transport Management

### Original Current State

Existing:

- `MCPServerSpec` models `stdio`, `http`, `sse`, and `streamable_http`.
- `MCPConnector.register_tool(...)` can register in-process callables.
- `MCPConnector.list_tools(...)` lists registered tools.
- `MCPConnector.call_tool(...)` policy-gates and executes registered callables.
- Provider-native remote MCP is modeled separately as `RemoteMCP` hosted tools.

Originally missing:

- Starting and supervising stdio MCP subprocesses.
- Connecting to HTTP/SSE and Streamable HTTP MCP servers.
- JSON-RPC request/response handling.
- MCP `initialize`, `tools/list`, and `tools/call`.
- Tool-list cache and invalidation.
- OAuth/token provider hooks for HTTP transports.
- Transport logs, audit records, and approval resume behavior.

### Public API

Keep the existing connector entry point:

```python
connector = MCPConnector([
    MCPServerSpec(
        name="tickets",
        transport="stdio",
        command="python",
        args=["-m", "ticket_mcp"],
    ),
])

tools = await connector.list_tools("tickets")
result = await connector.call_tool("tickets", "create_ticket", {"title": "Bug"})
```

Add lifecycle methods:

```python
await connector.start()
await connector.stop()
await connector.refresh_tools("tickets")
```

`list_tools` should lazily start the target server if `start()` was not called.

### Internal Modules

Add:

```text
src/agent_runtime/mcp/client.py
src/agent_runtime/mcp/transports.py
src/agent_runtime/mcp/auth.py
src/agent_runtime/mcp/cache.py
```

Core protocol:

```python
class MCPTransportClient(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any: ...
```

Concrete transports:

- `StdioMCPTransport`
- `StreamableHTTPMCPTransport`
- `LegacySSEMCPTransport`

Use the optional `[mcp]` dependency if it provides stable client primitives.
If the dependency API is not suitable, keep a minimal JSON-RPC client internal
to this package and preserve the optional dependency for future compatibility.

### MCP Server Spec Changes

Extend `MCPServerSpec` conservatively:

```python
@dataclass(slots=True, frozen=True)
class MCPServerSpec:
    name: str
    transport: MCPTransport
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout_seconds: float = 30.0
    tool_cache_ttl_seconds: float | None = 300.0
    require_approval: bool = False
    metadata: dict[str, str] = field(default_factory=dict)
```

Validation rules:

- `stdio` requires `command`.
- `http`, `sse`, and `streamable_http` require `url`.
- `command` must be executed without a shell.
- `headers` and `env` values are secret-capable and must be redacted in raw
  event sinks by default.

### Tool Discovery

`list_tools(server)` behavior:

1. Resolve the server.
2. Start and initialize its transport if needed.
3. Call `tools/list`.
4. Convert each MCP tool to `MCPToolDefinition`.
5. Cache definitions by server name and protocol session ID.
6. Emit `mcp.list_tools.completed`.

`refresh_tools(server)` bypasses cache and replaces cached definitions.

Cache invalidation:

- TTL expiry.
- `refresh_tools`.
- transport reconnect.
- provider/server notification if exposed by the client.

### Tool Calls

`call_tool(server, tool, arguments)` behavior:

1. Resolve the tool from cache, refreshing tools if missing.
2. Run `Policy.check` at `before_mcp_call`.
3. If denied, raise `MCPError`.
4. If approval is required, emit `mcp.approval.required` and raise
   `ApprovalError` until an approval channel is wired.
5. Call MCP `tools/call` over the transport.
6. Emit `mcp.call.started` and `mcp.call.completed` or `tool.call.failed`.
7. Preserve structured content, text content, image content, and raw result.

### Local Tool Bridge

Add an explicit bridge method to expose MCP tools to a local model loop without
making them indistinguishable from Python functions:

```python
tool_descriptors = await connector.to_runtime_tools()
```

Rules:

- Public names stay `mcp:<server>.<tool>`.
- Calls route back through `MCPConnector.call_tool`.
- Policy checkpoints still run before the MCP call.
- Tool descriptors include MCP metadata so apps can audit the server boundary.

### HTTP Auth

Add:

```python
class MCPAuthProvider(Protocol):
    async def authorization_header(self, server: MCPServerSpec) -> str | None: ...
```

Rules:

- Static headers in `MCPServerSpec.headers` are supported.
- Dynamic auth provider can add or refresh `Authorization`.
- Do not log auth headers.
- OAuth browser/device flows are out of scope for phase 1; accept externally
  obtained tokens.

### Security Requirements

- Stdio transport must use `asyncio.create_subprocess_exec`, not a shell.
- Stdio server stderr is captured as logs, not parsed as protocol output.
- Streamable HTTP client sends JSON-RPC over POST and supports SSE receive when
  the server uses it.
- Local HTTP URLs should default to localhost-only unless caller explicitly
  allows remote URLs through policy.
- Every MCP call emits auditable server/tool/argument metadata, with redaction
  for sensitive values.
- Approval-required behavior must fail closed until resume is implemented.

### Tests

Unit tests:

- `MCPServerSpec` validation for required command/url fields.
- stdio transport sends JSON-RPC lines and rejects non-protocol stdout.
- Streamable HTTP transport sends correct request headers and session ID.
- legacy SSE transport parses server events.
- auth provider injects and redacts authorization.
- tool cache hits, expires, refreshes, and invalidates on reconnect.
- policy allow/deny/approval paths still behave.

Runtime tests:

- fake stdio MCP server lists tools and handles `tools/call`.
- connector exposes MCP descriptors as `mcp:<server>.<tool>`.
- local agent loop can call an MCP-backed tool via the bridge.
- MCP call result becomes a normal tool result while preserving MCP metadata.

Integration tests:

- `tests/integration/mcp/test_stdio_smoke.py`.
- `tests/integration/mcp/test_streamable_http_smoke.py`.
- Mark with `integration_mcp`.
- Skip when no configured server URL/command is present.

## Implementation Sequence

1. Add cloud-agent fake client and session/event mapping tests.
2. Implement `ClaudeCodeAgentProvider` behind a client protocol.
3. Add cloud-agent integration smoke test and update capability docs.
4. Add MCP transport/client/cache/auth modules with fake transports.
5. Refactor `MCPConnector` to use real transports while preserving local
   callable registration for tests and in-process tools.
6. Add MCP local tool bridge into `AgentLoop` or `ToolRuntime`.
7. Update `FEATURES.md`, `README.md`, and `tests/VALIDATION.md`.

Each implementation slice must pass:

```bash
pytest -q
ruff check .
mypy src tests
```

## Acceptance Criteria

The milestone is complete when:

- At least one non-local `AgentProvider` can create/start/stream/cancel/resume
  a session against a fake client offline and a real provider in a gated smoke
  test.
- Cloud-agent capabilities are true only for implemented lifecycle methods.
- `MCPConnector` can start a stdio MCP server, list tools, call a tool, and
  shut the server down.
- `MCPConnector` can connect to a Streamable HTTP MCP server and preserve MCP
  session IDs.
- MCP calls are policy-gated, audited, and represented with canonical MCP
  events.
- Provider-native `RemoteMCP` hosted-tool mapping continues to work unchanged.

## Open Questions

1. Should `OpenAICloudAgentProvider` grow a deeper SandboxAgent workspace
   manifest bridge, or keep SDK-native sandbox configuration as pass-through?
2. Should MCP approval resume be implemented in this milestone or remain a
   fail-closed `ApprovalError` until the broader approval channel is unified?
3. Should remote HTTP MCP allow non-localhost URLs by default, or require an
   explicit policy allow decision?
