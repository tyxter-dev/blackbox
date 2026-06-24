# AgentProvider Workflow Validation

This plan validates a conversational AgentProvider workflow with runnable
examples instead of assertion-oriented tests.

## Workflow

| Workflow | Example | Success signal |
|---|---|---|
| Conversational support agent with retrieval | `examples/agent_provider_support_conversation.py` | Creates a temporary OpenAI vector store, configures a local `AgentProvider` with a support prompt and `FileSearch`, then returns multiple assistant messages in one agent turn. |

## How To Run

Install live OpenAI dependencies:

```bash
pip install -e .[openai]
```

Set credentials and, optionally, a model:

```bash
export OPENAI_API_KEY=...
export OPENAI_EXAMPLE_MODEL=gpt-4o-mini
```

The example also loads `examples/.env` when it exists. Model selection checks
`OPENAI_EXAMPLE_MODEL`, `OPENAI_AGENT_MODEL`, then `OPENAI_TEST_MODEL`.

PowerShell:

```powershell
$env:OPENAI_API_KEY = "..."
$env:OPENAI_EXAMPLE_MODEL = "gpt-4o-mini"
```

Run the workflow:

```bash
python examples/agent_provider_support_conversation.py
```

## Library Support Being Exercised

- `AgentSpec.instructions` remains the configured support-agent system prompt.
- `AgentSpec.hosted_tools` lets an AgentProvider-backed agent receive hosted
  retrieval tools without plumbing raw provider SDK clients through example code.
- `runtime.agents.run(..., agent=AgentSpec(...))` supports one-off agent
  definitions without a separate `create_agent(...)` call.
- `temporary_openai_file_search(...)` creates the temporary OpenAI vector store,
  waits for ingestion, yields a ready `FileSearch`, and cleans up resources.
- `conversational_response(...)` asks the provider for short chat-style
  replies and projects the final text into `AgentSessionResult.messages`, so
  apps do not need a structured `list[message]` output model for this workflow.
- `AgentSessionResult.summary()` exposes message count, usage, hosted-tool
  metadata, and source references for review.

## Review Checklist

- The example should read like application code, not test scaffolding.
- One-off agents should pass `AgentSpec` directly as `agent=...` instead of
  introducing a second `spec=` wrapper.
- The support prompt, retrieval setup, and response style should be configured
  on the agent rather than encoded as a Pydantic response model.
- The output should include at least two assistant messages from one agent turn.
- The vector store should be temporary and require no pre-provisioned
  `OPENAI_VECTOR_STORE_ID`.
