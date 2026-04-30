# agents

`agents` is now a compatibility package for older imports such as
`blackbox.agents.local`.

New code should import agent provider adapters from
`blackbox.providers.agent_adapters`.

The implementation modules moved so provider contracts and provider adapters
live under the same top-level package.
