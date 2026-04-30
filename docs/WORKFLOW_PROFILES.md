# Workflow Configuration Profiles

`RuntimeConfig` is a typed preset and override object for existing runtime
methods. It does not add a new runtime facade. Each profile expands into the
ordinary keyword arguments already accepted by `runtime.run(...)`,
`runtime.models.run(...)`, `runtime.agents.run(...)`, or
`runtime.realtime.connect(...)`.

```python
from blackbox import RuntimeConfig, WorkspaceSpec

config = RuntimeConfig.profile("coding_agent").with_overrides(
    provider="openai:gpt-5.5",
    workspace=WorkspaceSpec.git(url=repo_url, ref="main"),
)

result = await runtime.run(
    input="Refactor this module and run tests",
    config=config,
)
```

Explicit method arguments override config values. Profile defaults are applied
first, file or environment values can fill in the next layer, and
`with_overrides(...)` is intended for final application-level customization.

## Built-In Profiles

| Profile | Surfaces | Defaults | Required Values | Tradeoff |
| --- | --- | --- | --- | --- |
| `fast_text` | `model`, `runtime` | `temperature=0.2`, `max_output_tokens=512`; runtime also sets `max_iterations=1` | none | Optimized for short low-latency text. Not intended for tool use, strict schemas, or long reasoning. |
| `structured_extraction` | `model`, `runtime` | `temperature=0.0`; model surface defaults `output_strategy="provider_native"` | one of `output_spec`, `output_schema`, `output_type` | Requires a schema and favors strict output over creativity. Provider-native enforcement remains provider/model dependent. |
| `tool_agent` | `runtime` | `parallel_tool_calls=True`, `tool_selection="auto"`, bounded tool budget | one of `tools`, `toolsets`, `hosted_tools` | Adds orchestration overhead. Providers without function tools or parallel tool controls fail before SDK dispatch. |
| `retrieval_agent` | `model`, `runtime` | `tool_search.enabled=True`, `tool_search.max_results=5`, `compaction.strategy="auto"` | retrieval source or tool-search control | Better context discovery with added search latency and provider-specific support requirements. |
| `coding_agent` | `runtime` | dynamic tools, `max_iterations=8`, ephemeral cache, auto compaction, `approval_policy="risky_actions"` | `workspace` | Assumes workspace access and exposes a broader tool surface. Write and command actions should stay policy-gated. |
| `cloud_agent_session` | `agent_session` | `metadata.workflow_profile="cloud_agent_session"` | `agent` | Delegates lifecycle and tool behavior to the agent provider. Direct model-turn controls do not apply. |
| `realtime_voice` | `realtime` | WebSocket transport, manual tool mode, text/audio input, text/audio output, `audio.voice="alloy"` | none | Requires a realtime provider and audio-capable model. |
| `eval_run` | `model`, `runtime` | `temperature=0.0`, `parallel_tool_calls=False`; runtime adds `context_flags=["eval"]` | none | Low variance makes evals easier to compare but can reduce exploration. |
| `cost_sensitive` | `model`, `runtime` | `temperature=0.2`, `max_output_tokens=1024`, cache strategy `auto`; runtime limits iterations and visible tools | none | Lower spend can truncate long answers or under-expose tools. |
| `high_reliability` | `model`, `runtime` | `temperature=0.0`, `parallel_tool_calls=False`, `reasoning_effort="high"`; runtime sets `max_iterations=12` | none | More careful and deterministic, but often slower and provider/model dependent. |

Profile metadata is available at runtime:

```python
from blackbox import workflow_profile_docs

docs = workflow_profile_docs()
print(docs["coding_agent"]["tradeoffs"])
```

## Loading Config

Python object:

```python
config = RuntimeConfig.profile("fast_text").with_overrides(
    provider="openai:gpt-5.5",
    max_output_tokens=256,
)
```

Environment:

```powershell
$env:AGENT_RUNTIME_PROFILE = "cost_sensitive"
$env:AGENT_RUNTIME_MODEL = "openai:gpt-5.5"
$env:AGENT_RUNTIME_MAX_OUTPUT_TOKENS = "512"
$env:AGENT_RUNTIME_CACHE_STRATEGY = "ephemeral"
```

```python
config = RuntimeConfig.from_env()
result = await runtime.run(input="Summarize this", config=config)
```

TOML:

```toml
profile = "coding_agent"
model = "openai:gpt-5.5"
workspace = { kind = "git", url = "https://example.com/org/repo.git", ref = "main" }

[cache]
strategy = "ephemeral"
```

```python
config = RuntimeConfig.from_file("runtime.toml")
result = await runtime.run(input="Run the task", config=config)
```

JSON uses the same field names. YAML is supported only when PyYAML is installed;
without it, loading a `.yaml` or `.yml` file raises `ConfigurationError` with an
install hint.

## Validation

`RuntimeConfig.to_kwargs(surface=...)` validates profile surface compatibility
and required values before runtime dispatch. Provider/model capability checks
then use the existing capability profile path, so unsupported controls, hosted
tools, output strategies, state modes, and cross-feature constraints still fail
before SDK calls.

Provider-qualified models are normalized for existing APIs:

```python
RuntimeConfig.profile("fast_text").with_overrides(
    model="openai:gpt-5.5",
).to_kwargs(surface="runtime")
# {"provider": "openai:gpt-5.5", ...}
```

If both `provider` and a provider-qualified `model` are set and disagree,
`ConfigurationError` is raised.
