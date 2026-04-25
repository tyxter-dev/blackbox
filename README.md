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

## Minimal usage

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

## Tests

```bash
pip install -e .[dev]
pytest                          # offline suite
pytest -m integration_openai    # network-gated, requires OPENAI_API_KEY
```

## Next implementation targets

1. Anthropic Messages-native `ModelProvider`.
2. Gemini GenerateContent-native `ModelProvider`.
3. OpenAI cloud / Codex-style `AgentProvider`.
4. Anthropic Managed Agents `AgentProvider`.
5. Google Agent Platform `AgentProvider`.
6. MCP local connector + provider-native remote MCP wiring.
7. Workspace abstraction for repo/sandbox/cloud workspaces and patches.

