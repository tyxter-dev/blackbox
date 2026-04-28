# providers

`providers` defines provider contracts, the provider registry, and provider
adapter implementations. It is the package to read when you want to understand
how runtime requests cross the boundary into model APIs or agent-session APIs.

## Belongs Here

- `ModelProvider` and `AgentProvider` protocols.
- Request/result contracts used at provider boundaries.
- Provider routing and registry utilities.
- Provider-neutral request controls.
- Model-turn adapter implementations in `model_adapters/`.

## Does Not Belong Here

- Runtime loop orchestration.
- Workspace execution backends.
- Local Python tool execution.

## Adapter Locations

- `model_adapters/`: direct `ModelProvider` adapters such as OpenAI Responses,
  Anthropic Messages, Gemini Generate Content, xAI Responses, and Echo.
- `agent_adapters/`: `AgentProvider` adapters such as local agents, Claude Code,
  OpenAI Agents SDK, and Vertex AI Agent Engine.
