# Journey Tests

Journey tests exercise user-facing workflows and emit reviewable reports. They
are grouped by the runtime framework or business workflow they validate.

## Directories

- `model_provider/`: direct model-turn journeys and hosted-tool journeys.
- `agent_provider/`: local and provider-managed agent-session journeys.
- `workspace_provider/`: direct and runtime-integrated workspace journeys.
- `business_cases/`: domain-oriented examples that validate full application
  workflows, prompt planning, and tool/runtime parity.

Use the README inside each directory for marker-specific commands and required
environment variables.
