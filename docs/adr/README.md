# Architecture Decision Records

Each file records **one** settled architectural decision: the context, the
decision, its consequences, and the alternatives that were rejected. ADRs are
immutable once accepted — to change a decision, add a new ADR that supersedes the
old one (and mark the old one `Superseded by ADR-XXXX`).

These are the "don't re-litigate" decisions. They were previously scattered as
prose across `AGENTS.md` (Hard constraints / Deferred decisions), `docs/PRD.md`
§3–§4 and §26, and the `ROADMAP.md` decision log; this folder makes each one
individually addressable. Those sources remain the narrative; the ADRs are the
canonical per-decision record. Slice-level history stays in `CHANGELOG.md`.

## Index

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-no-litellm-in-house-registry.md) | No LiteLLM; in-house `ProviderRegistry` | Accepted |
| [0002](0002-chat-messages-are-a-projection.md) | Chat messages are a compatibility projection, not canonical state | Accepted |
| [0003](0003-separate-model-and-agent-protocols.md) | Separate `ModelProvider` and `AgentProvider` protocols (cloud agents are not tools) | Accepted |
| [0004](0004-preserve-raw-provider-payloads.md) | Preserve raw provider payloads (provider-native escape hatches) | Accepted |
| [0005](0005-provider-native-provider-state.md) | `ProviderState` is provider-native, not chat history | Accepted |
| [0006](0006-zero-dependency-core-pydantic-optional.md) | Zero-dependency core; dataclasses in core, Pydantic optional | Accepted |
| [0007](0007-four-output-strategies-fail-fast.md) | Four structured-output strategies; fail-fast validation default | Accepted |
| [0008](0008-colon-routing-separator.md) | `:` is the canonical `provider:model` routing separator | Accepted |
| [0009](0009-mcp-trust-security-boundary.md) | MCP trust & risk as an enforced security boundary | Accepted |
| [0010](0010-workspaces-and-persistence-in-core.md) | Workspaces and persistence interfaces are core in v0.1 | Accepted |
| [0011](0011-import-name-blackbox-dist-name-deferred.md) | Import name `blackbox`; PyPI distribution name deferred | Accepted |

## Format

Each ADR carries: **Status**, **Recorded** date, **Sources** (where the decision
was originally argued), then **Context / Decision / Consequences / Alternatives
considered**. Keep them short — a decision, not an essay.

> These ADRs were recorded retroactively (2026-06-24) from existing settled
> decisions; the decisions themselves were made during the 2026-04/05 design and
> implementation work.
