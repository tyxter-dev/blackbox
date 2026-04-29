# AgentProvider Framework PRD

**Framework:** `AgentProvider`
**Parent PRD:** `PRD.md` (Agent Runtime Core)
**Document status:** Framework PRD, implementation-aligned draft
**Last reviewed against code:** 2026-04-26

## 1. Product Purpose

`AgentProvider` is the provider-native framework for supervising agent sessions:
local model-backed agents, SDK-backed coding agents, and future cloud-managed
agent engines.

It exists because an agent session is not the same thing as one model turn. A
session has its own lifecycle, event stream, approvals, cancellation state,
follow-up invocations, artifacts, provider-native IDs, and sometimes a provider
managed workspace. The runtime should supervise those sessions without forcing
them through a fake chat-completion or local-tool abstraction.

The product promise is:

```text
Applications can create or reference an agent session, stream canonical events,
pause/resume for approvals, cancel work, send follow-up messages, and list
artifacts while provider-native session details remain available.
```

## 2. Relationship To The Core PRD

The root PRD defines three major surfaces:

```text
ModelProvider    -> runs model turns
AgentProvider    -> supervises agent sessions
WorkspaceProvider -> gives agents places to work
```

This document expands the `AgentProvider` portion. It should be treated as the
guide for agent-session behavior and provider adapter work, not as a replacement
for the high-level `AgentRuntime.run(...)` task runner.

`runtime.run(...)` remains the everyday blackbox path. `runtime.agents.*` is the
lower-level supervision surface for sessions that need explicit lifecycle
control.

## 3. Framework Boundary

### In Scope

`AgentProvider` owns:

```text
agent definition creation
session creation and session reference restoration
session event streaming
session status transitions
follow-up invocations
approval routing
cancellation
artifact listing
provider-native session/run/checkpoint references
provider-native event payload preservation
session-level capability reporting
```

### Out Of Scope

`AgentProvider` does not own:

```text
single model turn execution
local Python tool execution
workspace filesystem and sandbox semantics
workspace package registry and publishing
business workspace UI, teams, billing, tenancy, or RBAC
OAuth vaults and connector credential storage
scheduler or queue infrastructure
```

A local agent provider can delegate to `AgentLoop`. A cloud or SDK provider can
delegate to its provider SDK. In both cases, the `AgentProvider` boundary is
session supervision.

## 4. Current Implementation Snapshot

| Area | Current evidence | State |
|---|---|---|
| Core protocol | `src/agent_runtime/providers/base.py` | Implemented. `AgentProvider` exposes create/start/stream/follow-up/approve/cancel/artifacts. |
| Runtime facade | `src/agent_runtime/runtime.py` | Implemented. `runtime.agents` routes provider calls and stamps streamed events with run/trace metadata. |
| Local provider | `src/agent_runtime/agents/local.py` | Implemented. Uses `AgentLoop` for model/tool execution and session-shaped lifecycle. |
| OpenAI Agents provider | `src/agent_runtime/agents/openai_cloud.py` | Implemented as an optional `openai-agents` SDK wrapper or injected client. |
| Claude Code provider | `src/agent_runtime/agents/claude_code.py` | Implemented as an optional `claude-agent-sdk` wrapper or injected client. |
| Vertex AI Agent Engine | `src/agent_runtime/agents/vertex_agent_engine.py` | Scaffold only. Raises typed unsupported errors until a real adapter lands. |
| Capabilities | `src/agent_runtime/core/capabilities.py` | Implemented. `AgentCapabilities` advertises sessions, events, artifacts, workspace, approvals, MCP, cancellation, and resume. |
| Session refs | `src/agent_runtime/core/sessions.py` | Implemented. `AgentRef`, `SessionRef`, `InvocationRef`, and `AgentSession` are public contracts. |
| Events | `src/agent_runtime/core/events.py` | Implemented. Session, cloud-agent, workspace, approval, model, tool, MCP, artifact, handoff, guardrail, retry, and eval event names exist. |
| Tests | `tests/runtime/test_local_agent_provider.py`, `tests/runtime/test_openai_agents_provider.py`, `tests/runtime/test_claude_code_agent_provider.py`, `tests/contracts/test_capability_honesty.py` | Current behavior is covered with local, injected-client, SDK-wrapper, and capability tests. |

## 5. Primary Users

| User | Need | Value |
|---|---|---|
| Technical founder | Switch between local and managed coding agents | One supervision layer for very different execution models. |
| Backend engineer | Start, stream, cancel, and resume agent work | Session lifecycle API instead of custom orchestration glue. |
| Agent platform engineer | Compare provider-managed agents | Canonical events plus raw provider payloads. |
| QA/evaluation engineer | Replay and inspect runs | Session events, artifacts, refs, and trace metadata. |
| Product engineer | Add approvals and human review | Standard approval events and decisions across providers. |

## 6. Core Workflows

### A1: Run A Local Model-Backed Agent Session

```python
agent = await runtime.agents.create_agent(
    provider="local",
    spec=AgentSpec(
        name="support-agent",
        model="openai:gpt-5.4",
        tools=["lookup_customer", "create_ticket"],
    ),
)

session = await runtime.agents.create_session(
    provider="local",
    agent=agent,
    task="Handle this support request.",
    model="openai:gpt-5.4",
)

async for event in runtime.agents.stream(session):
    ...
```

The provider owns the session shell. `AgentLoop` owns local model/tool
iteration.

### A2: Supervise A Provider-Managed Coding Agent

```python
agent = await runtime.agents.create_agent(
    provider="claude-code",
    spec=AgentSpec(
        name="repo-maintainer",
        instructions="Fix the repo and return artifacts.",
        tools=["Read", "Write", "Bash"],
        permissions={"permission_mode": "default"},
    ),
)

session = await runtime.agents.create_session(
    provider="claude-code",
    agent=agent,
    task=TaskSpec(
        prompt="Fix the failing test.",
        workspace=WorkspaceSpec.local("./repo"),
    ),
)
```

Provider-managed tools, workspace operations, and approvals should map into the
runtime stream without pretending the cloud agent is a local function tool.

### A3: Collect A Provider-Managed Agent Result

```python
result = await runtime.agents.run(
    provider="claude-code",
    agent="repo-maintainer",
    task="Fix the failing test.",
    workspace=WorkspaceSpec.local("./repo"),
    output_type=PatchSummary,
)
```

The collector starts a session, stores stamped events, lists artifacts, exposes
a strict result `status`, keeps the `SessionRef` for follow-up work, and
validates final session text into the requested output type.

### A4: Pause For Approval And Resume

```python
async for event in runtime.agents.stream(session):
    if event.type == "approval.requested":
        await runtime.agents.approve(
            provider=session.provider,
            approval_id=event.data["approval_id"],
            decision=ApprovalDecision(approved=True, reason="Safe to continue."),
        )
```

The provider may implement approval natively, or the local `AgentLoop` may pause
before executing a policy-gated tool or workspace operation.

### A5: Send Follow-Up Work

```python
invocation = await runtime.agents.send_message(
    session,
    "Run the tests again after applying the patch.",
)
```

Every follow-up returns an `InvocationRef` so applications can correlate future
events and artifacts with the user action that caused them.

### A6: Cancel Or List Artifacts

```python
await runtime.agents.cancel(session)

page = await runtime.agents.list_artifacts(session, type="patch")
```

Providers that cannot honor a lifecycle operation must fail with a typed
unsupported error instead of silently doing nothing.

## 7. Public Contract

The current protocol is:

```python
@runtime_checkable
class AgentProvider(Protocol):
    @property
    def provider_id(self) -> str:
        ...

    def capabilities(self) -> AgentCapabilities:
        ...

    async def create_agent(self, spec: AgentSpec) -> AgentRef:
        ...

    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession:
        ...

    def stream_events(
        self,
        session: SessionRef | AgentSession,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        ...

    async def send_message(
        self,
        session: SessionRef | AgentSession,
        message: str,
    ) -> InvocationRef:
        ...

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        ...

    async def cancel(self, session: SessionRef | AgentSession) -> None:
        ...

    async def list_artifacts(
        self,
        session: SessionRef | AgentSession,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        ...
```

`AgentSpec` defines local or hosted agents with name, instructions, model,
local tools, hosted tools, MCP servers, environment, permissions, response
projection, and metadata. `TaskSpec` defines prompt, optional model, workspace,
input artifacts, task-scoped hosted tools, response override, metadata, and
provider specific `extra`.

## 8. Event Taxonomy

`AgentProvider` adapters should map common lifecycle concepts to these canonical
events:

```text
session.created
session.started
session.completed
session.failed
session.cancelled
cloud_agent.status.changed
cloud_agent.log
cloud_agent.checkpoint.created
agent.response.message.created
model.request.started
model.item.created
model.text.delta
model.reasoning.delta
model.completed
tool.call.started
tool.call.completed
hosted_tool.call.requested
hosted_tool.call.completed
mcp.call.started
mcp.call.completed
workspace.file.read
workspace.file.changed
workspace.command.started
workspace.command.completed
approval.requested
approval.approved
approval.denied
artifact.created
artifact.updated
```

Adapters may emit additional provider-specific event names only when no
canonical event fits. Raw provider payloads belong in `AgentEvent.raw` or
provider-specific `data` fields.

## 9. Requirements

### P0 - Required

| ID | Requirement | Current state | Acceptance criteria |
|---|---|---|---|
| A-P0-1 | Stable provider identity | Implemented | Every provider exposes `provider_id` and registry routing can resolve it. |
| A-P0-2 | Capability honesty | Implemented | Capability flags match supported operations; unsupported features raise typed errors. |
| A-P0-3 | Create agent | Implemented | `create_agent(...)` returns `AgentRef` with provider and stable id. |
| A-P0-4 | Start session | Implemented | `start_session(...)` returns `AgentSession` with provider, task, status, agent id, model, and metadata. |
| A-P0-5 | Stream events | Implemented | `stream_events(...)` yields `AgentEvent` objects and preserves raw provider payloads where available. |
| A-P0-6 | Session status transitions | Implemented | Providers update created/running/waiting/completed/failed/cancelled consistently. |
| A-P0-7 | Approval bridge | Implemented for local loop and SDK wrappers | Permission requests emit `approval.requested`; decisions emit approved/denied and resume or deny the action. |
| A-P0-8 | Cancellation | Implemented where supported | Providers cancel or raise `UnsupportedFeatureError` according to advertised capability. |
| A-P0-9 | Artifact listing | Implemented, provider-dependent | `list_artifacts(...)` always returns `ArtifactPage`; local currently returns an empty page. |
| A-P0-10 | Runtime facade | Implemented | `runtime.agents.*` delegates to providers and stamps streamed events with runtime trace metadata. |
| A-P0-11 | Local provider | Implemented | `LocalAgentProvider` delegates to `AgentLoop` and does not fork a second local-agent loop. |

### P1 - Important

| ID | Requirement | Current state | Acceptance criteria |
|---|---|---|---|
| A-P1-1 | Event replay cursor | Partial | `after_event_id` should work consistently across local and cloud providers. |
| A-P1-2 | Follow-up messages | Partial | `send_message(...)` should define provider-neutral behavior for running, waiting, and completed sessions. |
| A-P1-3 | Provider-native resume | Partial | `SessionRef.metadata` and provider session IDs can restore sessions, but durable resumption semantics need one standard. |
| A-P1-4 | Workspace handoff | Partial | `TaskSpec.workspace` reaches capable providers; local provider should integrate with `WorkspaceProvider` without ad hoc tool setup. |
| A-P1-5 | MCP handoff | Partial | `AgentSpec.mcp_servers` is part of the contract; providers map it where their SDK supports MCP. |
| A-P1-6 | Artifact normalization | Partial | Provider files, patches, logs, checkpoints, screenshots, and reports normalize into `Artifact` records. |
| A-P1-7 | Session persistence | Partial | `runtime.agents.run(...)` saves session refs, status, provider state, and replay cursors into `RunStore`; a dedicated session store is still open. |
| A-P1-8 | Agent result collector | Implemented | `runtime.agents.run(...)` returns `AgentSessionResult[T]` with typed output, text, strict status, events, artifacts, session ref, provider state, usage, trace, and metadata. |

### P2 - Later

| ID | Requirement | Acceptance criteria |
|---|---|---|
| A-P2-1 | Multi-agent handoffs | Explicit handoff events and ownership transfer between providers. |
| A-P2-2 | Guardrail events | Provider-native and runtime guardrails normalize into `guardrail.*` events. |
| A-P2-3 | Eval hooks | Sessions can emit or attach evaluation results. |
| A-P2-4 | Deployment lifecycle | Provider-specific deploy/version/eval APIs live outside the minimal session contract. |
| A-P2-5 | Background supervision | Long-running runs can be supervised through an external queue/store. |

## 10. Non-Goals

`AgentProvider` must not:

```text
be reduced to ModelProvider
be modeled as a local Python tool
own workspace sandbox internals
turn every provider session into synthetic chat history
hide provider-native event payloads
implement downstream admin, team, billing, or scheduling products
```

## 11. Gaps And Decisions

1. **Replay cursor consistency:** `after_event_id` is part of the protocol, but
   the local provider should either implement it or clearly advertise that it is
   unsupported.
2. **Local artifact collection:** Local sessions currently return an empty
   artifact page even when the underlying loop can emit artifacts. Define how
   those artifacts are stored by session.
3. **Session persistence:** `runtime.agents.run(...)` writes a compact
   `RunState`; decide whether richer multi-invocation history belongs in a new
   `SessionStore`.
4. **Workspace handoff:** Route `TaskSpec.workspace` through the
   `WorkspaceProvider` layer for local sessions instead of requiring users to
   manually register workspace tools.
5. **Vertex scope:** Keep Vertex AI Agent Engine as a scaffold until its exact
   session, event, artifact, eval, and deployment contract is implemented and
   tested.

## 12. Definition Of Done

The framework is successful when:

```text
1. Local, OpenAI Agents SDK, Claude Code SDK, and Vertex Agent Engine providers
   can share one session contract without hiding provider-native state.
2. Every provider reports capabilities conservatively.
3. Session streams map provider lifecycle, model, tool, workspace, approval, and
   artifact activity to canonical events.
4. Approval and cancellation work where supported and fail loudly where not.
5. Provider-native session references survive through SessionRef metadata.
6. Artifact listing is consistent across local and cloud providers.
7. The high-level runtime remains simple: runtime.run(...) for ordinary tasks,
   runtime.agents.* for explicit session supervision.
```
