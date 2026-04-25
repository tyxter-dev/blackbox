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
  mcp/              # MCP server specs and connector placeholder
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
    output_type=TicketDecision,
)

decision: TicketDecision = result.output
print(decision.priority, result.payloads)
```

`AgentResult[T]` carries the validated `output`, the final `text` projection,
the full event/run-item/artifact log, the tool `payloads` (for the deferred
payload pattern: tools that return content for the model and structured data
for the application), and the final `provider_state` for resumption.

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
`tool_search_*`, `mcp_*`, reasoning) get canonical event types; evolving hosted
tools fall back to `MODEL_ITEM_CREATED` with the original item type stashed in
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

## Workspace runtime (M2 local)

`WorkspaceRuntime` backs `"local"` workspaces with file read/write/delete,
patch application, command execution, and snapshotting. It emits canonical
workspace events (`WORKSPACE_FILE_READ`, `WORKSPACE_FILE_CHANGED`,
`WORKSPACE_COMMAND_STARTED/_COMPLETED`, `WORKSPACE_PATCH_CREATED`,
`ARTIFACT_CREATED`) and consults a `Policy` at three M2 checkpoints:
`before_workspace_write`, `before_command`, `before_artifact_export`.

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
root). Cloud/sandbox/git workspace kinds raise `WorkspaceError` until their
adapters land.

## Tests

```bash
pip install -e .[dev]
pytest                            # offline suite
pytest -m integration_openai      # network-gated, requires OPENAI_API_KEY
pytest -m integration_anthropic   # network-gated, requires ANTHROPIC_API_KEY
```

## Next implementation targets

1. Gemini GenerateContent-native `ModelProvider`.
2. OpenAI cloud / Codex-style `AgentProvider`.
3. Claude Code `AgentProvider`.
4. Vertex AI Agent Engine `AgentProvider`.
5. MCP local connector + provider-native remote MCP wiring.
6. AgentLoop integration of workspace ops as a tool backend with approval
   flow wired through.

