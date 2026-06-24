# 04 — Feature: Agent Providers (sessions)

**Protocol:** `AgentProvider` · **Facade:** `runtime.agents` · **Adapters:**
`src/blackbox/providers/agent_adapters/`

## Summary

An `AgentProvider` runs **sessions** — long-lived, resumable, event-streaming
units of work that may be a local model-backed runtime or a provider-managed
cloud agent. Sessions are first-class resources, not model calls with more tools.
The normalized event/session/artifact contract over heterogeneous agent backends
is the moderately differentiated middle layer of the project.

```python
session = await runtime.agents.create_session(
    provider="local", agent="repo-maintainer",
    task="Find why CI is failing and prepare a patch.", model="openai:gpt-5.5",
)
async for event in runtime.agents.stream(session):
    print(event.type, event.data)
```

## The protocol

```python
class AgentProvider(Protocol):
    @property
    def provider_id(self) -> str: ...
    def capabilities(self) -> AgentCapabilities: ...
    async def create_agent(self, spec: AgentSpec) -> AgentRef: ...
    async def start_session(self, agent: AgentRef | str, task: TaskSpec) -> AgentSession: ...
    async def stream_events(self, session: SessionRef | AgentSession) -> AsyncIterator[AgentEvent]: ...
    async def send_message(self, session: SessionRef | AgentSession, message: str) -> None: ...
    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None: ...
    async def cancel(self, session: SessionRef | AgentSession) -> None: ...
    async def list_artifacts(self, session: SessionRef | AgentSession) -> list[Artifact]: ...
```

Required behavior: treat sessions as first-class; stream session/model/tool/
cloud-agent/workspace/approval/artifact events; preserve provider session IDs and
raw payloads; support cancellation/resumption/artifacts **only when advertised**.

## Sessions

`AgentSession` statuses: `created`, `running`, `waiting`, `completed`, `failed`,
`cancelled`. `waiting` is the pause used for approvals, custom tool results, user
clarification, or provider-side pauses. Sessions are addressed by `SessionRef`,
`AgentRef`, and `InvocationRef`.

## AgentSessionResult[T]

The collected output of a high-level `runtime.agents.run(...)` provider-managed
session:

- `output` — validated structured output of type `T`, or plain text.
- `text` — final session text projection.
- `status` — strict collector status: `completed`, `failed`, `cancelled`,
  `waiting_for_approval`, or `timeout`.
- `events` — collected and stored `AgentEvent` stream.
- `artifacts` — from inline events and provider artifact pages.
- `session_ref` — handle for follow-ups, replay, artifact listing.
- `provider_state` — native continuation synthesized from session metadata/cursor.
- `usage` — normalized usage when exposed.
- `trace` — reconstructed workflow trace metadata.
- `metadata` — session, usage, artifact, and diagnostic metadata.

## Adapters

| Adapter | Surface | Notes |
|---|---|---|
| `LocalAgentProvider` | local model-backed | Delegates to the same `AgentLoop` as `runtime.run`. The reference implementation. |
| `OpenAICloudAgentProvider` | OpenAI Agents SDK / Codex-style | Cloud/coding-agent sessions, sandboxed agents; thread/session creation, run/resume/cancel where available; streams logs, workspace changes, commands, patches, artifacts; preserves thread IDs + raw payloads; local SDK wrapper mode when cloud API isn't available. |
| `ClaudeCodeAgentProvider` | Claude Code / Agent SDK | Headless mode + sessions, file ops, code execution, web search, MCP extensibility, permissions, custom tool-use pauses, tool-confirmation approvals, resumption/checkpoints. Forwards `setting_sources` (project skills). Four concepts: agent, environment, session, events. |
| `VertexAIAgentEngineProvider` | Vertex AI Agent Engine | Cloud-managed sessions, memory, evaluation, deployment, monitoring; CLI/lifecycle wrapped as typed events/artifacts. **Currently an honest stub** — implement or explicitly descope (Horizon 1). |

Deprecated aliases `AnthropicManagedAgentProvider` and
`GoogleAgentPlatformProvider` are exported temporarily — don't use in new code.

## Reserved: delegation (P2)

Two delegation patterns are reserved as event constants, not yet implemented, and
explicitly **not** modeled as fake function calls:

- **Agent handoffs** — `handoff.requested/.started/.completed/.failed`: one agent
  delegates to another specialized agent.
- **Agent-as-tool** — `agent_tool.call.started/.completed`: expose another agent
  as a callable tool while preserving session and trace boundaries.

## Requirements

| ID | Priority | Requirement |
|---|---|---|
| P0-R3 | P0 | Local agent provider implements create/start/stream/cancel/list-artifacts. |
| P0-R5 | P0 | Sessions expose ID, provider, status, task, model, metadata, refs. |
| P1-R4 | P1 | OpenAI cloud agent provider starts/resumes a session and streams status/artifacts. |
| P1-R5 | P1 | Claude Code provider supports agent/environment/session/events and approval pauses. |
| P1-R6 | P1 | Vertex/Google provider wraps create/run/eval/deploy/observe. |
| P1-R7 | P1 | Session artifacts can be listed and typed consistently. |
| P1-R13 | P1 | `runtime.agents.run(...)` returns `AgentSessionResult[T]` (typed output, text, strict status, events, artifacts, ref, state, usage, trace, metadata). |
| P2-R9 | P2 | Agent handoffs (reserved events). |
| P2-R10 | P2 | Agent-as-tool (reserved events). |

## Hard constraints

- **Cloud agents are first-class** — never modeled as ordinary tools.
- Cancellation/resumption/artifacts are honored **only if advertised**
  (capability honesty).
- Preserve provider thread/session IDs and raw event payloads.
- Renaming `AgentProvider`, `AgentSession`, or `SessionRef` is "ask first".

## Status & references

Priority order delivered: `OpenAICloudAgentProvider` → `ClaudeCodeAgentProvider`
→ `VertexAIAgentEngineProvider` (stub). Cloud agent webhook ingress is
contract-only (`AgentWebhookProvider`, `runtime.agents.ingest_webhook`) — needs
one real verifying implementation (Horizon 1). Docs: `docs/AGENT.md`,
`docs/validation/AGENT_PROVIDER_WORKFLOW_VALIDATION.md`. PRD §9.2, §13.2/13.4/13.6.

→ Next: [05 — Tools](05-tools.md)
