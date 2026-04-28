# AgentProvider Journeys

These tests exercise the `AgentProvider` framework as user-facing journeys.
They intentionally avoid deterministic assertions against model text. Local
AgentProvider journeys use real configured model providers, and optional
provider-managed journeys run only when explicitly enabled.

Run with:

```bash
pytest -m journey_agent_provider -s tests/journey/agent_provider
```

## Covered Journeys

The suite covers these user-facing goals:

- `local_agent_session_lifecycle`: a user creates a local model-backed agent, starts a session, streams events, and receives a completed session.
- `local_agent_follow_up_message`: a user sends a follow-up message into an existing session and receives an invocation reference.
- `local_agent_tool_dispatch`: a user gives a local agent tools and expects the agent to call them through `AgentRuntime`.
- `local_agent_approval_resume`: a user requires approval before a sensitive local tool call and resumes the session after approval.
- `local_agent_cancel_after_tool_output`: a user cancels an active local session after enough work has been observed.
- `local_agent_artifact_listing`: a user lists artifacts through the AgentProvider contract, even when the local provider returns an empty page.
- `agent_provider_capability_report`: a user inspects capability flags before choosing a provider.
- `openai_agents_sdk_session_supervision`: a user supervises an OpenAI Agents SDK-backed session and lists artifacts.
- `claude_code_session_supervision`: a user supervises a Claude Code / Agent SDK-backed session and lists artifacts.

Provider credentials are read from the standard model environment variables:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` or `GEMINI_API_KEY`,
and `XAI_API_KEY`.

Optional provider-managed journeys require explicit opt-in:

- `RUN_OPENAI_AGENT_PROVIDER_JOURNEY=1` for OpenAI Agents SDK supervision.
- `RUN_CLAUDE_CODE_AGENT_PROVIDER_JOURNEY=1` for Claude Code supervision.

Use `AGENT_PROVIDER_JOURNEY_MODEL_PROVIDERS=openai,google` to limit the model
provider set used by local AgentProvider journeys.
