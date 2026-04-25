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

## Next implementation targets

1. Implement OpenAI Responses-native `ModelProvider`.
2. Add provider-native event mappings for tool calls, hosted tools, tool search, remote MCP, and reasoning items.
3. Implement an OpenAI cloud-agent `AgentProvider` or SDK wrapper.
4. Implement Google Agent Platform wrapper as an `AgentProvider`.
5. Implement Anthropic Managed Agents wrapper as an `AgentProvider`.
6. Keep LiteLLM out of the core; optionally add a compatibility provider later.

