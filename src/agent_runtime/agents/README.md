# agents

`agents` is now a compatibility package for older imports such as
`agent_runtime.agents.local`.

New code should import agent provider adapters from
`agent_runtime.providers.agent_adapters`.

The implementation modules moved so provider contracts and provider adapters
live under the same top-level package.
