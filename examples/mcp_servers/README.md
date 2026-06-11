# Fake external MCP servers (Tier 0)

Offline stand-ins for external MCP providers, per the validation ladder in
`docs/USE_CASE_VALIDATION.md`. Real external MCP integrations require
accounts, API keys, or OAuth; these servers let MCP examples and tests
exercise the *entire* real integration path — managed stdio transport,
JSON-RPC initialize, `tools/list` discovery, trust policy, namespaced
dispatch, structured results, error shapes, canonical MCP events — with zero
credentials.

They are also a dogfood of blackbox's own `MCPServer` authoring SDK
(`blackbox.mcp.server`).

## Rule

An MCP example that cannot run against one of these fakes (Tier 0) does not
merge. Live keys (Tier 2) and OAuth (Tier 3) add confidence; Tier 0 defines
correctness.

## Servers

| File | Stands in for | Tools |
|---|---|---|
| `fake_crm.py` | A CRM SaaS (account + API key) | `lookup_customer`, `list_open_deals`, `create_followup_task` |
| `fake_booking.py` | A scheduling SaaS (OAuth) | `list_slots`, `book_slot`, `cancel_booking` |
| `fake_maps.py` | A maps provider (API key), mirroring the Google Maps MCP shape in `launchmybakery.py` | `geocode`, `nearby_places` |

Each server includes at least one error path raised as `MCPToolError`, so
callers can validate `isError` tool-result handling, not just happy paths.

## Usage

Point a managed stdio `MCPServerSpec` at the script:

```python
import sys

from blackbox import (
    MCPApprovalMode, MCPServerSpec, MCPServerTrustPolicy, MCPToolset, MCPTrustLevel,
)

toolset = MCPToolset(
    server=MCPServerSpec(
        name="fake-crm",
        transport="stdio",
        command=sys.executable,
        args=["examples/mcp_servers/fake_crm.py"],
        trust_policy=MCPServerTrustPolicy(
            server="fake-crm",
            trust_level=MCPTrustLevel.FIRST_PARTY,
            approval_mode=MCPApprovalMode.NEVER,
        ),
    ),
    mode="local",
)

result = await runtime.run(..., toolsets=[toolset])
```

See `examples/mcp_toolset_fake_crm.py` for the complete runnable flow.

Each server also runs standalone for manual poking (line-delimited JSON-RPC
on stdio):

```bash
python examples/mcp_servers/fake_crm.py
{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
```
