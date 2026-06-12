# Use-Case Validation

Validates the library's examples and contracts against real-world agent
deployments, and defines how integration-heavy examples (MCP, external
providers) get validated without requiring accounts, API keys, or OAuth for
every run.

## 1. Ground truth

Source: an anonymized aggregate of a production agent platform export
(2026-04-15): **610 deployed conversational agents across 283 tenants**,
WhatsApp-centric, CRM-integrated. Only aggregate statistics appear here; the
raw export stays out of the repository.

Key aggregate facts:

- **42 distinct tools** in use; typical agents carry **30–40 tools each**
  (CRM: tasks, deals, customers, calendar, reminders, automations, message/
  email templates, reports, CSV query, generic external API calls).
- **574/610** agents carry a `browse_toolkit` tool — runtime tool discovery
  over a large catalog is the *norm*, not the exception.
- **588/610** can escalate to a human (`call_human`); **103** transfer to
  another agent; auto-routing between agents is enabled on all 610.
- **~570** schedule/cancel/reactivate reminders; **295** initiate the
  conversation on session start; idle-nudge timers and operating hours exist.
- All 610 persist full conversation memory across sessions.
- Mixed provider fleet in one platform (OpenAI + xAI); **170 agents were
  running a model already past its announced retirement date** at export
  time.
- **Zero MCP usage.** Every integration is an in-house tool or a generic
  HTTP tool (`call_external_api`, 578 agents).

## 2. What the data validates about the design

The production patterns land directly on bets the library already made:

| Production pattern | Library bet confirmed |
|---|---|
| 30–40 tool catalogs + `browse_toolkit` | `Toolset`, `ToolBudget`, `tool_selection="dynamic"`, `ToolCatalog` |
| `call_human` escalation everywhere | Approval events/decisions as first-class runtime citizens |
| Reminders, session-start initiation, operating hours | `ScheduleSpec`/`ScheduleTrigger` on the agent package |
| Full-memory persistent conversations | `ProviderState` + `SessionStore` resumption design |
| CRM writes returning data to the app, prose to the model | `ToolResult.content`/`payload` separation |
| Mixed OpenAI/xAI fleet under one platform | Provider registry + capability profiles |
| Agents running retired models in production | `ProviderModelCatalog` lifecycle/replacement metadata |
| Multi-tenant SaaS reselling model usage | Pricing catalog: provider cost vs `billable` + `MarkupPolicy` |

And one corrective finding: **external MCP is aspirational, not present, in
this dataset.** Real deployments integrate via in-house tools and generic
HTTP. Conclusion for validation strategy: MCP examples must never be the
gate for validating the library's core value, and their validation must not
depend on manually provisioned accounts (§5).

## 3. Coverage matrix

Status: ✅ covered by a runnable example · 🟡 partial (tests or README
snippet only) · ❌ no example.

| Real-world pattern | Prevalence | Library surface | Example today | Status |
|---|---|---|---|---|
| Blackbox loop, typed output | all | `runtime.run`, `AgentResult[T]` | `minimal_runtime_run`, `run_with_typed_output` | ✅ |
| Large catalog + dynamic tool loading | 574/610 | `Toolset`, `ToolBudget`, `tool_selection="dynamic"` | tests only (`test_dynamic_toolsets`) | ❌ **top gap** |
| Human escalation / approval pause | 588/610 | `ApprovalRequest`/`ApprovalDecision`, policy gates | none dedicated | ❌ |
| Agent-to-agent transfer / routing | 103 + routing on all | handoff events/items; routing itself is app-level | none | ❌ (and document the core/app boundary) |
| Scheduled & proactive behavior | ~570 | `ScheduleSpec`, `ScheduleTrigger`, `run_workspace_agent` | README snippet only | 🟡 needs executor (Roadmap H2) |
| Persistent conversation memory | 610/610 | `provider_state` resume, `SessionStore`, `MemorySpec` | tests only (`test_resume_run_from_saved_state`) | 🟡 |
| RAG / knowledge lookup | ~40 | hosted `FileSearch` | `model_provider_knowledge_drawer` | ✅ |
| Generic external HTTP API tool | 578/610 | local tools | `local_agent_with_tool` (generic) | 🟡 worth a dedicated example |
| Media in/out (images, documents) | 580/610 | `ContentPart`s, `MediaRef` | none | ❌ |
| Deferred payload pattern (CRM writes) | ~650 calls/agent | `ToolResult.payload` | `run_with_typed_output` | ✅ |
| Multi-provider fleet | platform-wide | registry, `ProviderRef` | implicit in all examples | ✅ |
| Tenant cost accounting / resale markup | platform-wide | pricing catalog, `MarkupPolicy`, `billable` | README snippet only | 🟡 |
| Model lifecycle / deprecation alerts | 170 agents on a retired model | `ProviderModelCatalog` lifecycle | none | ❌ cheap, high-value |
| External MCP integrations | 0 | `MCPToolset`, `RemoteMCP`, `MCPConnector` | `launchmybakery` (needs Google credentials) | 🟡 credential-gated; see §5 |

## 4. Example backlog (priority order)

1. ✅ `dynamic_toolset_crm.py` — a CRM-shaped 31-tool catalog with
   `tool_selection="dynamic"` and a `ToolBudget`; mirrors the single most
   common production shape. Offline (scripted model). Building it exposed
   and fixed a real dispatch bug: dynamically loaded tools were visible to
   the model but rejected at execution because the dispatch gate held a
   frozen copy of the initially visible tool set.
2. ✅ `human_escalation.py` — a policy marks `issue_refund` as requiring
   approval; the run pauses on `APPROVAL_REQUESTED`, an out-of-band reviewer
   resolves via `runtime.approve(...)`, and both the approved and denied
   paths complete.
3. ✅ `conversation_resume.py` — provider-native continuation state
   checkpointed to `SQLiteRunStore` and reloaded by a fresh runtime in a
   simulated process restart; the resumed model recalls a fact that only
   exists in the state loaded from disk.
4. ✅ `model_lifecycle_audit.py` — audits a simulated fleet against
   `ProviderModelCatalog`, flagging the model past its deprecation deadline
   with its replacement, plus models missing from the bundled catalog
   (which doubles as evidence for the catalog-refresh roadmap item).
5. ✅ `tenant_billing.py` — provider cost vs billable with `MarkupPolicy`
   across simulated tenant runs, with a per-tenant invoice rollup and
   margin.
6. `media_messages.py` — image/document content parts in and out.
7. `agent_handoff.py` — transfer between two local agents, emitting
   canonical handoff events; documents that *routing policy* belongs to the
   application.
8. `external_api_tool.py` — the generic HTTP tool pattern done safely
   (timeouts, payload separation, schema).

Each example follows the existing conventions: offline by default
(echo/scripted providers), `examples/.env` loading only where live providers
are explicitly the point.

## 5. MCP external-provider validation strategy

The problem: MCP integration examples currently require real accounts, API
keys, or end-user OAuth (`launchmybakery` needs Google ADC + a Maps key),
which makes them expensive to validate and impossible to CI. The fix is a
four-tier ladder where each tier is cheaper than the one below and validates
strictly more than nothing:

### Tier 0 — Offline fake MCP servers (default; runs in CI)

✅ Shipped: `examples/mcp_servers/` contains fake CRM, booking, and maps
servers authored with the library's own `MCPServer` stdio SDK — realistic
tool schemas, canned data, and `MCPToolError` error paths.
`examples/mcp_toolset_fake_crm.py` runs the full connector path (managed
stdio transport, discovery, trust policy, namespaced dispatch, canonical MCP
events) against the fake CRM with zero accounts. Every future MCP example
defaults to a fake server and accepts a flag/env switch for the real one.
This dogfoods the authoring SDK while validating the connector.

### Tier 1 — Golden contract fixtures

Record real servers' `tools/list` and `tools/call` response shapes once and
replay them as golden tests (`tests/golden/` already establishes this
pattern for model providers). Catches schema drift without network access.

### Tier 2 — Key-based live validation (`integration_mcp` markers)

Integrations needing only an API key or service account — no human OAuth
dance. Gated by the existing marker + collection-time deselection mechanism.
Maintain one credential manifest here so setup is a checklist, not
archaeology:

| Integration | Env vars | How to obtain | Notes |
|---|---|---|---|
| Google Maps MCP | `MAPS_API_KEY` | GCP console, API-key restricted to Maps | used by `launchmybakery` |
| Google BigQuery MCP | `GOOGLE_CLOUD_PROJECT` + ADC | `gcloud auth application-default login` | service-account JSON also works |
| GitHub MCP | `GITHUB_TOKEN` | fine-grained PAT, read-only scopes | good remote-MCP smoke target |
| Stripe MCP | `STRIPE_SECRET_KEY` (test mode) | Stripe dashboard test keys | test mode is free and safe |

Key provisioning can itself be scripted (e.g. API-key provisioning services
such as projects.dev) if this tier grows.

### Tier 3 — End-user OAuth (manual; journey cadence only)

The genuinely expensive tier (`ConnectorSpec(auth_mode="end_user")`-shaped
integrations). Keep it minimal: one setup script per integration that runs
the OAuth flow once and caches the refresh token into `examples/.env`.
Validate on journey cadence (pre-release sweeps), never in CI. The friction
in this tier is the strongest argument for the Roadmap Horizon 2 *connector
auth contract* — the library should define how packaged agents reference
credentials precisely so applications, not examples, own the OAuth dance.

### Rule

An MCP example that cannot run at Tier 0 does not merge. Tiers 1–3 add
confidence; Tier 0 defines correctness.

## 6. Maintenance

Re-run this analysis when a fresh production export is available; the
archetype mix (§1) and coverage matrix (§3) are snapshots. Keep `FEATURES.md`
as the status source for library behavior; this document tracks *demand-side*
coverage — whether the examples prove the library against what agents
actually do in production.
