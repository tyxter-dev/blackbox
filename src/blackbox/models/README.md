---
status: active
owner: blackbox-src
since: 2026-06-27
adr: docs/adr/0003-separate-model-and-agent-protocols.md
prd: docs/prd/03-model-providers.md
---

# models

`models` is now a compatibility package for older imports such as
`blackbox.models.openai_responses`.

New code should import model provider adapters from
`blackbox.providers.model_adapters`.
