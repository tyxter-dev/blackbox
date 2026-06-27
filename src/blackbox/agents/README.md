---
status: active
owner: blackbox-src
since: 2026-06-27
adr: docs/adr/0003-separate-model-and-agent-protocols.md
prd: docs/prd/04-agent-providers.md
---

# agents

`agents` is now a compatibility package for older imports such as
`blackbox.agents.local`.

New code should import agent provider adapters from
`blackbox.providers.agent_adapters`.

The implementation modules moved so provider contracts and provider adapters
live under the same top-level package.
