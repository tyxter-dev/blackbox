# mcp

`mcp` owns Model Context Protocol integration. It connects MCP servers to the
runtime either as local runtime-managed toolsets or as provider-native remote MCP
declarations.

## Belongs Here

- MCP server specs and safety validation.
- MCP server authoring helpers for first-party stdio adapters.
- MCP auth providers and auth challenges.
- MCP clients, connectors, tool cache, and transports.
- Runtime MCP toolset routing.
- Conversion from local MCP specs to provider-native remote MCP declarations.

## Does Not Belong Here

- Generic local Python tool execution.
- Provider adapter event mapping except for MCP-specific conversion helpers.
- Business-domain connector definitions.

## Boundary Note

MCP tools can become runtime-executed tools or provider-native hosted tools
depending on provider capabilities. The MCP package owns that routing metadata;
the model adapters still own final provider API mapping.

## Production Compatibility Layer

- `compat.py` declares the MCP spec-version compatibility matrix. `MCPClient`
  negotiates only versions in that matrix and stops the transport on unsupported
  negotiated versions.
- Remote HTTP auth uses `MCPAuthProvider` / `OAuthBearerMCPAuthProvider`.
  401/403 challenges can refresh a bearer token once; remaining failures raise
  `MCPAuthenticationError` with server, status, scope, resource metadata URL,
  and retry safety fields.
- `MCPTrustPolicyPresets` provides named trust postures:
  `local_only`, `enterprise_remote`, and `provider_native_allowed`.
- Discovery cache keys include server, session, protocol, tool filters, trust
  fingerprint, auth identity, and one-way credential/header fingerprints. They
  never store raw tokens. Runtime-managed `MCPToolset` instances keep a
  per-toolset local discovery cache so repeated runs can reuse schemas without
  re-listing tools. `notifications/tools/list_changed` invalidates cached
  discovery and clears stale managed tool descriptors.
- MCP policy requests and approval events include server/tool/ref, scopes,
  risks, trust level, route mode, and trust fingerprint so applications can make
  approval decisions using the same event stream as local tools.
- MCP call failure events redact by default; output limits apply first from
  per-tool policy, then from server policy/spec defaults.

## Server Authoring

`server.py` provides a small first-party authoring layer for local stdio MCP
servers. It is intentionally narrow: register tools, expose JSON schemas,
validate optional Pydantic input models, normalize structured results, and serve
the line-delimited stdio protocol used by blackbox transports.

```python
from pydantic import BaseModel, Field

from blackbox.mcp import MCPServer


class CreateCharge(BaseModel):
    amount: int = Field(gt=0)
    description: str


server = MCPServer("payments")


@server.tool(description="Create a payment charge.", input_model=CreateCharge)
def create_charge(args: CreateCharge) -> dict[str, object]:
    return {"amount": args.amount, "status": "pending"}


raise SystemExit(server.run_stdio())
```

Tool handler failures that should reach the caller as MCP tool errors can raise
`MCPToolError`. Unexpected handler exceptions are contained as `isError` tool
results instead of crashing the server process.
