# models

`models` is now a compatibility package for older imports such as
`agent_runtime.models.openai_responses`.

New code should import model provider adapters from
`agent_runtime.providers.model_adapters`.
