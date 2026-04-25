# Architecture Notes

## Why two provider protocols?

`ModelProvider` and `AgentProvider` are intentionally separate because hosted/cloud agents are not model calls with more tools. They have lifecycle, artifacts, permissions, state, sessions, approvals, and sometimes deployment/evaluation operations.

## Provider compatibility without LiteLLM

This scaffold takes the routing idea but not the dependency or format normalization:

- Providers register themselves by key.
- Provider refs parse strings like `openai/gpt-5` or `google-agent/my-agent`.
- Capabilities are explicit.
- Provider-native state is stored in `ProviderState` and adapter-specific metadata.
- Events and items are normalized; native payloads are preserved.

## First implementation milestone

1. Implement OpenAI Responses-native `ModelProvider`.
2. Keep `EchoModelProvider` as the test provider.
3. Add a provider-native mapping table from OpenAI output item types to `RunItem` and `AgentEvent`.
4. Add tool dispatch from `RunItem(type="function_call")` to `ToolRuntime`.
5. Add remote MCP and cloud-agent adapters only after the event/state model proves stable.
