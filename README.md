# Agent Runtime Core

A fresh scaffold for a provider-native agent runtime.

The design deliberately does **not** use LiteLLM. It borrows only the useful idea of provider compatibility through routing, capabilities, and a common public interface. The core is not a Chat Completions normalizer. The core is built around sessions, events, state, artifacts, and provider-native escape hatches.

## Heart of the library

```text
ModelProvider
  Runs model turns.
  Examples: OpenAI Responses, Anthropic Messages, Gemini GenerateContent.

AgentProvider
  Runs agent sessions.
  Examples: OpenAI cloud coding agents, Anthropic managed agents, Google Agent Platform,
  local model-backed agents.

Workspace contracts
  Describe where agents work and how packaged workspace agents are governed.
  Examples: WorkspaceRuntime, WorkspaceAgentSpec, ConnectorSpec, ScheduleSpec.
```

## Core principle

```text
Do not normalize providers into chat messages internally.
Normalize them into typed events and run items, while preserving provider-native state.
```

Chat messages can be an import/export compatibility layer later, but they should not become the runtime truth.

## Package layout

```text
src/agent_runtime/
  core/             # events, items, state, sessions, capabilities, artifacts, approvals
  providers/        # ModelProvider and AgentProvider protocols + registry
  models/           # model provider adapters; EchoModelProvider included for tests
  agents/           # cloud/local agent provider adapters; LocalAgentProvider included
  tools/            # local tool registry/runtime/context injection
  workspaces/       # workspace abstractions for files, patches, repos, sandboxes
  workspace_agents/ # governed agent package contracts, permissions, schedules, registry
  mcp/              # MCP server specs, connector, and stdio/HTTP transports
  observability/    # traces and event sink placeholders
```

## High-level blackbox API

The product promise from `llm_factory_toolkit` v1 is preserved on top of the
provider-native primitives: give the runtime a task, tools, and an output
schema; get back a validated typed result. The runtime owns the tool/model
loop end-to-end.

```python
from pydantic import BaseModel

from agent_runtime import AgentRuntime
from agent_runtime.models.openai_responses import OpenAIResponsesProvider


class TicketDecision(BaseModel):
    should_escalate: bool
    priority: str
    summary: str


runtime = AgentRuntime()
runtime.registry.register_model(OpenAIResponsesProvider(api_key="..."))
runtime.tools.register(create_ticket)
runtime.tools.register(search_customer)

result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Review this customer report and create a ticket if needed.",
    tools=["search_customer", "create_ticket"],
    tool_max_concurrent=2,
    tool_timeout=10.0,
    output_type=TicketDecision,
)

decision: TicketDecision = result.output
print(decision.priority, result.payloads)
```

`AgentResult[T]` carries the validated `output`, the final `text` projection,
the full event/run-item/artifact log, the tool `payloads` (for the deferred
payload pattern: tools that return content for the model and structured data
for the application), and the final `provider_state` for resumption.

For one-off tools, create an isolated tool session. It starts with the global
tool registry, accepts temporary registrations, and does not leak those tools
back into later runs:

```python
tool_session = runtime.tools.session()
tool_session.register(lookup_ticket, name="lookup_ticket")

result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Check ticket T-1",
    tools=["lookup_ticket"],
    tool_session=tool_session,
)
```

`tool_max_concurrent` and `tool_timeout` apply to local tool execution in the
blackbox loop, including dynamic tool sessions.

## Provider-hosted tools

Provider-hosted tools stay separate from local Python tools. Pass typed hosted
tool specs through `hosted_tools`; the provider adapter maps them to native
tool configuration without registering fake local callables:

```python
from agent_runtime import AgentRuntime, FileSearch, RemoteMCP, WebSearch

runtime = AgentRuntime()

result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Research this policy and cite relevant files.",
    hosted_tools=[
        WebSearch(search_context_size="medium"),
        FileSearch(vector_store_ids=["vs_123"], include_results=True),
        RemoteMCP(
            server_label="github",
            server_url="https://mcp.example.com/sse",
            allowed_tools=["list_issues", "get_pull_request"],
            require_approval="never",
        ),
    ],
)
```

OpenAI Responses maps `WebSearch`, `FileSearch`, and `CodeInterpreter` to
native `tools` entries, and maps `RemoteMCP` to the provider-native MCP tool.
Anthropic maps `RemoteMCP` to `mcp_servers` plus an `mcp_toolset`. Gemini
currently maps `WebSearch` to `google_search`. Use `HostedToolRaw` as an
explicit escape hatch for provider-specific hosted tool payloads.

When a provider returns MCP tool-list, call, or approval items, the runtime
normalizes them into MCP run items and adds an `mcp` summary to result
metadata. For OpenAI, `ProviderState.native_history` also preserves native
`mcp_list_tools` items so callers can continue a workflow without forcing the
provider to list the same MCP tools again.

## Lower-level model turns

```python
from agent_runtime import AgentRuntime
from agent_runtime.models.echo import EchoModelProvider

runtime = AgentRuntime()
runtime.registry.register_model(EchoModelProvider())

result = await runtime.models.run(
    provider="echo",
    model="echo-mini",
    input="hello",
)
print(result.text)
```

Applications can inspect granular provider/model support before constructing a
request:

```python
profile = runtime.models.capabilities("openai:gpt-5.4")

assert profile.hosted_tools["web_search"].status == "supported"
assert profile.output_strategies["provider_native"].status == "supported"
assert profile.controls["reasoning_effort"].status == "supported"
```

The same profile powers runtime validation. If a request explicitly asks for
an unsupported hosted tool, output strategy, control, or state mode, the
runtime raises `UnsupportedFeatureError` before making the provider SDK call.
`HostedToolRaw` and `extra` remain explicit provider-native escape hatches.

## Chat compatibility facade

Chat messages are supported as an explicit compatibility projection:

```python
from agent_runtime import ChatMessage

result = await runtime.chat.run(
    provider="openai:gpt-5.4",
    messages=[
        ChatMessage(role="system", content="Be concise."),
        ChatMessage(role="user", content="Summarize this incident."),
    ],
)
```

The facade converts messages into a provider input payload and then delegates
to `runtime.models`; chat messages do not become the runtime's internal event
or state model.

## Local agent usage

```python
from agent_runtime import AgentRuntime
from agent_runtime.agents.local import LocalAgentProvider
from agent_runtime.models.echo import EchoModelProvider

runtime = AgentRuntime()
runtime.registry.register_model(EchoModelProvider())
runtime.registry.register_agent(LocalAgentProvider(runtime.models))

session = await runtime.agents.create_session(
    provider="local",
    agent="default",
    task="Explain this traceback",
    model="echo/echo-mini",
)

async for event in runtime.agents.stream(session):
    print(event.type, event.data)
```

Use `runtime.agents.run(...)` when you want the managed session collected into
one result:

```python
result = await runtime.agents.run(
    provider="local",
    agent="default",
    task="Explain this traceback",
    model="echo/echo-mini",
)

print(result.text)
print(result.status)
print(result.session_ref.id)
```

Runnable AgentProvider examples live in `examples/`:

- `examples/agent_provider_local_session.py` runs a local model-backed agent
  session, streams events, sends a follow-up message, and streams again.
- `examples/local_agent_with_tool.py` shows the local provider driving a tool
  call loop through `ToolRuntime`.
- `examples/agent_provider_openai_agents.py` shows an OpenAI Agents
  SDK-backed provider session, event streaming, artifact listing, and saving
  returned artifacts under `examples/artifacts/openai/`.
- `examples/agent_provider_claude_code.py` shows a Claude Code provider run
  against a local workspace with typed `AgentSessionResult` output and saved
  artifacts under `examples/artifacts/claude-code/`.

## Workspace agent packages

`WorkspaceAgentSpec` describes a governed agent as a portable package:
instructions, model/provider preference, tools, hosted tools, MCP servers,
connectors, permissions, schedules, skills, memory policy, publication
metadata, version, and owner metadata. The package contract lives in core, while
admin UI, OAuth, org RBAC, cron execution, secret storage, and persistence stay
in downstream applications.

```python
from agent_runtime import (
    AgentRuntime,
    ConnectorSpec,
    ScheduleSpec,
    ScheduleTrigger,
    ToolPermission,
    WorkspaceAgentSpec,
    run_workspace_agent,
)
from agent_runtime.models.echo import EchoModelProvider

runtime = AgentRuntime()
runtime.registry.register_model(EchoModelProvider())

agent = WorkspaceAgentSpec(
    name="release-prep",
    instructions="Prepare a concise release brief.",
    model_provider="echo",
    model="echo-mini",
    tools=["lookup_issue"],
    connectors=[ConnectorSpec(name="github", kind="github", auth_mode="end_user")],
    permissions=[ToolPermission(ref="lookup_issue", scopes=["read"], connector="github")],
    schedules=[
        ScheduleSpec(
            name="weekday",
            trigger=ScheduleTrigger(kind="cron", expression="0 16 * * 1-5"),
        )
    ],
)

result = await run_workspace_agent(runtime, agent, input="Summarize today's work.")
```

## Examples

Runnable scripts live under `examples/`:

- `examples/run_with_typed_output.py` — high-level blackbox loop with a
  Pydantic `output_type`, tool dispatch, and deferred payload collection.
- `examples/echo_run.py` — minimal model turn with the dependency-free echo provider.
- `examples/local_agent_with_tool.py` — local agent driving a tool-call loop end-to-end.
- `examples/openai_responses_run.py` — provider-native OpenAI Responses streaming
  (requires `OPENAI_API_KEY` and `pip install -e .[openai]`).

## OpenAI Responses-native model provider

The first real `ModelProvider` is implemented and wires the Responses API
event stream into typed `AgentEvent`s. Stable items (`message`, `function_call`,
`tool_search_*`, `mcp_*`, reasoning) get canonical event types; known hosted
tools such as web search, file search, code interpreter, computer use, and image
generation become `hosted_tool_call` run items. Unknown provider items fall back
to `MODEL_ITEM_CREATED` with the original item type stashed in
`data["item_type"]` and the raw payload preserved on `event.raw`.
`ProviderState.previous_response_id` round-trips for multi-turn continuations.

```python
from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.models.openai_responses import OpenAIResponsesProvider

runtime = AgentRuntime()
runtime.registry.register_model(OpenAIResponsesProvider(api_key="..."))

async for event in runtime.models.stream(
    provider="openai", model="gpt-4o-mini", input="Review this patch."
):
    if event.type == EventTypes.MODEL_TEXT_DELTA:
        print(event.data["delta"], end="")
```

## xAI Responses model provider

`XAIResponsesProvider` reuses the OpenAI-compatible Responses adapter against
`https://api.x.ai/v1`. It preserves xAI response IDs in `ProviderState`, maps
Responses stream events into the same runtime events, and keeps conservative
capability flags for surfaces that are not wired as native xAI features here
(`tool_search`, client-executed search, and remote MCP).

```python
from agent_runtime import AgentRuntime
from agent_runtime.models.xai_responses import XAIResponsesProvider

runtime = AgentRuntime()
runtime.registry.register_model(XAIResponsesProvider(api_key="..."))

result = await runtime.models.run(
    provider="xai", model="grok-4", input="Reply with exactly: pong"
)
```

## Provider-native request controls

Direct model calls and the high-level loop accept common controls and map them
to provider-native request fields where supported:

```python
result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Summarize the incident.",
    instructions="Use concise operational language.",
    temperature=0.2,
    max_output_tokens=400,
    reasoning_effort="low",
)
```

OpenAI-specific typed controls include provider tool search and Responses
truncation:

```python
from agent_runtime import CompactionControl, ToolSearchControl

result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Use the available tool catalog to plan the next action.",
    tool_search=ToolSearchControl(max_results=5),
    compaction=CompactionControl(strategy="auto"),
)
```

Pass additional keyword arguments to `runtime.models.run(...)` for
provider-specific fields that are not part of the common controls. Those
provider-specific values override the common controls.

## Usage accounting

Provider adapters normalize token usage onto `result.metadata["usage"]` when
the provider returns usage data. The same usage view includes cache read/write
token splits, reasoning tokens where available, and runtime-counted tool calls.
Register prices in `runtime.model_catalog` to also receive
`result.metadata["cost"]`:

```python
from agent_runtime import ModelPricing

runtime.model_catalog.register_pricing(ModelPricing(
    provider="openai",
    model="gpt-5.4",
    input_per_million=1.25,
    output_per_million=10.00,
))
```

The runtime does not ship hard-coded prices; applications should register the
current prices they want to account against.

Cache usage is summarized in `result.metadata["cache"]` when cache controls are
requested or the provider reports cached tokens. Explicit cache keys and
provider cache IDs are tracked in `runtime.caches`, backed by either the default
in-memory registry or `SQLiteProviderCacheStore`:

```python
from agent_runtime import ModelCacheControl, SQLiteProviderCacheStore

runtime = AgentRuntime(provider_cache_store=SQLiteProviderCacheStore("caches.sqlite"))
result = await runtime.models.run(
    provider="openai:gpt-5.4",
    input=prompt,
    cache=ModelCacheControl(key="tenant-a", ttl="24h"),
)

print(result.metadata["cache"]["hit"])
print((await runtime.caches.get(provider="openai", model="gpt-5.4", key="tenant-a")).hits)
```

For providers with native cache lifecycle APIs, `runtime.caches.create(...)` and
`runtime.caches.invalidate(..., delete_provider_cache=True)` bridge to the
provider. The Gemini adapter supports context cache create/delete through
`client.caches.create/delete`.

## Anthropic Messages-native model provider

The second real `ModelProvider` is implemented and wires the Messages API
streaming events into typed `AgentEvent`s. `text` and `thinking` blocks emit
`MODEL_TEXT_DELTA` / `MODEL_REASONING_DELTA`; `tool_use` blocks accumulate
`input_json_delta` chunks and emit `TOOL_CALL_REQUESTED` once the block
closes; other blocks (`server_tool_use`, `web_search_tool_result`, future
hosted tools) fall back to `MODEL_ITEM_CREATED` with the raw block preserved
on `event.raw`. `ProviderState.native_history` carries the full Anthropic
`messages` array for multi-turn continuation.

```python
from agent_runtime import AgentRuntime, EventTypes
from agent_runtime.models.anthropic_messages import AnthropicMessagesProvider

runtime = AgentRuntime()
runtime.registry.register_model(AnthropicMessagesProvider(api_key="..."))

async for event in runtime.models.stream(
    provider="anthropic", model="claude-haiku-4-5-20251001", input="Review this patch.",
):
    if event.type == EventTypes.MODEL_TEXT_DELTA:
        print(event.data["delta"], end="")
```

## Persistence and resume

`JSONLEventStore` and `SQLiteRunStore` provide non-memory backends for
the `EventStore` and `RunStore` Protocols. Use them to persist event
logs to disk and resume an in-flight run from a checkpoint:

```python
from agent_runtime import AgentRuntime
from agent_runtime.core.state import RunState
from agent_runtime.core.stores import JSONLEventStore, SQLiteRunStore

runtime = AgentRuntime(
    event_store=JSONLEventStore("./events.jsonl"),
    run_store=SQLiteRunStore("./runs.sqlite"),
)

# Run, then save the captured provider_state for later.
result = await runtime.run(provider="openai:gpt-5.4", input="kick off")
await runtime.run_store.save(RunState(
    provider="openai", model="gpt-5.4",
    provider_state=result.provider_state,
    metadata={"phase": "checkpoint"},
))

# Later — possibly in a fresh process — reload and continue.
state = await runtime.run_store.load(saved_session_id)
follow_up = await runtime.run(
    provider="openai:gpt-5.4",
    input="continue from where we left off",
    provider_state=state.provider_state,
)
```

`AgentRuntime.run` and `AgentRuntime.stream` accept an optional
`provider_state` argument that flows through the loop into the adapter's
next `TurnRequest`, so resumption uses the provider's native
continuation IDs (`previous_response_id`, Anthropic message history,
etc.).

## Structured output with retry

Pass an `OutputSpec` to ask the runtime to repair its own output when
validation fails:

```python
from agent_runtime.core.results import OutputSpec

result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Decide priority for ticket #42.",
    output_spec=OutputSpec(
        schema=Decision,
        strategy="posthoc_parse_with_retry",
        max_validation_retries=2,
    ),
)
print(result.metadata["validation_attempts"])  # 1, 2, or 3
```

On validation failure the runtime feeds the bad text and the validator
error back into the model via a repair prompt, up to
`max_validation_retries` extra attempts. The plain `output_type=Cls`
shortcut still works and uses the default `posthoc_parse` strategy
(fail-fast).

## Redacting sensitive raw payloads

Wrap an event sink with `RedactingEventSink` to scrub `RawEnvelope`-tagged
payloads before they reach logs or telemetry:

```python
from agent_runtime.observability import MemoryEventSink, RedactingEventSink

sink = RedactingEventSink(inner=MemoryEventSink())  # default policy
async for event in runtime.stream(provider="...", input="..."):
    await sink.emit(event)
```

The default policy redacts when `sensitivity ∈ {sensitive, secret}` or
`storage_allowed=False`. Pass a custom `policy=Callable[[RawEnvelope],
bool]` for project-specific rules.

## Workflow tracing, replay, and eval hooks

Every runtime-stamped event carries `trace_id`, `span_id`,
`parent_span_id`, and `span_kind`. The default trace id is the run id, and
provider-native request/trace identifiers are preserved separately on the
event when available.

```python
from agent_runtime.observability import (
    OpenTelemetryTraceExporter,
    evaluate_trace,
    replay_run,
    trace_from_events,
)

result = await runtime.run(provider="openai:gpt-test", input="Ping")
trace = trace_from_events(result.events, metadata=result.metadata)

# Reconstruct a persisted run without calling a model.
replay = await replay_run(runtime.event_store, result.events[0].run_id)
print("\n".join(replay.timeline()))

# Export through the OpenTelemetry SDK configured by the application.
OpenTelemetryTraceExporter().export(trace)
```

`result.metadata["trace"]` includes a workflow root span plus child spans
for model calls, tool calls, hosted tools, MCP calls, workspace actions,
approvals, artifacts, retries, handoffs, guardrails, and eval events.
Evaluator hooks can score reconstructed traces and emit canonical
`eval.started` / `eval.completed` / `eval.failed` events.

## Workspace providers

`runtime.workspaces` is the first-class provider layer for coding-agent
workspaces. Local directories, git checkouts, sandbox clients, Docker-backed
sandboxes, and opaque cloud workspace refs share the `WorkspaceProvider`
contract. Provider-managed agents receive a resolved `WorkspaceRef` through
`TaskSpec.workspace`; provider-native file, command, patch, test, snapshot,
approval, and artifact events are normalized into the runtime event taxonomy.

```python
from agent_runtime.workspaces import WorkspaceSpec

workspace = await runtime.workspaces.open(
    WorkspaceSpec.git(url="https://example.com/org/repo.git", ref="main")
)

result = await runtime.agents.run(
    provider="openai-agents",
    agent="coding-agent",
    task="Implement the feature and return a patch.",
    workspace=workspace,
)
```

The direct provider API remains available for orchestration:

```python
from agent_runtime.workspaces import (
    CommandSpec, Patch, FileChange, WorkspaceRuntime, WorkspaceSpec,
)

ws_runtime = WorkspaceRuntime()
ws = await ws_runtime.open(WorkspaceSpec.local("/path/to/repo"))

await ws_runtime.write_file(ws, "notes.md", "# scratch")
result = await ws_runtime.run_command(ws, CommandSpec(command="pytest -q"))
patch = await ws_runtime.apply_patch(ws, Patch(
    summary="add greeting",
    diff="--- a\n+++ b\n",
    changes=[FileChange(path="hello.txt", type="create", content="hi")],
))
```

Path traversal is blocked (relative paths cannot escape the workspace
root). Workspace operations can also be exposed to the high-level model loop as
a run-scoped compatibility tool bridge:

```python
workspace_tools = runtime.tools.register_workspace(ws_runtime, ws)
result = await runtime.run(
    provider="openai:gpt-5.4",
    input="Read notes.md and summarize it.",
    tools=[tool.name for tool in workspace_tools],
)
```

## MCP local connector

`MCPConnector` can either register in-process MCP tools or manage an MCP
transport for a configured server. Managed servers are initialized with
JSON-RPC, listed through `tools/list`, cached, called through `tools/call`,
policy-gated, and surfaced as canonical MCP events.

```python
from agent_runtime.mcp import MCPConnector, MCPServerSpec

connector = MCPConnector([MCPServerSpec(name="tickets", transport="stdio")])
connector.register_tool("tickets", "lookup", lambda ticket_id: {"id": ticket_id})

tools = await connector.list_tools()
result = await connector.call_tool("tickets", "mcp:tickets.lookup", {"ticket_id": "T-1"})
events = connector.drain_events()
```

For a managed stdio server, provide the command:

```python
connector = MCPConnector([
    MCPServerSpec(
        name="tickets",
        transport="stdio",
        command="python",
        args=["-m", "ticket_mcp"],
    )
])

tools = await connector.list_tools("tickets")
result = await connector.call_tool("tickets", "lookup", {"ticket_id": "T-1"})
await connector.stop()
```

Discovered MCP tools can also be bridged into a local `ToolRegistry`:

```python
await connector.register_runtime_tools(runtime.tools.registry)
```

`MCPToolset` routes a configured MCP server through the high-level runtime.
`mode="local"` exposes discovered tools as namespaced local tools, while
`mode="provider_native"` passes a remote server to providers that support
native MCP. `mode="auto"` keeps stdio and private/local URLs in the runtime
and uses provider-native remote MCP only when the provider capability profile
supports it.

```python
from agent_runtime.mcp import MCPServerSpec, MCPToolset

github = MCPToolset(
    server=MCPServerSpec(
        name="github",
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
        allowed_tools=["list_issues"],
        require_approval="auto",
    ),
    mode="auto",
)

result = await runtime.run(
    provider="openai/gpt-5.4",
    input="Summarize recent auth issues.",
    toolsets=[github],
)
```

## Tests

```bash
pip install -e .[dev]
pytest                            # offline suite
pytest -m integration_openai      # network-gated, requires OPENAI_API_KEY
pytest -m integration_xai         # network-gated, requires XAI_API_KEY
pytest -m integration_anthropic   # network-gated, requires ANTHROPIC_API_KEY
pytest -m integration_gemini      # network-gated, requires GOOGLE_API_KEY
```

Integration tests load a repo-root `.env` only when an integration marker or
`tests/integration/...` path is selected, so the default `pytest` run remains
offline.

## Next implementation targets

1. Vertex AI Agent Engine `AgentProvider`.
2. Connector-level pending-call store for direct MCP approval resume outside
   the high-level runtime loop.
