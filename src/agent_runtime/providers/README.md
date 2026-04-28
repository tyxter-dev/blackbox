# providers

`providers` defines the provider contracts and registry. It should describe how
the runtime talks to external or local execution providers, without containing
the provider-specific API mapping itself.

## Belongs Here

- `ModelProvider` and `AgentProvider` protocols.
- Request/result contracts used at provider boundaries.
- Provider routing and registry utilities.
- Provider-neutral request controls.

## Does Not Belong Here

- OpenAI, Anthropic, Gemini, xAI, Claude Code, or Vertex adapter code.
- Runtime loop orchestration.
- Workspace execution backends.

## Current Adapter Locations

Model adapters currently live in `models/`. Agent adapters currently live in
`agents/`. A future structure pass can move those under provider adapter
subpackages while preserving compatibility imports.
