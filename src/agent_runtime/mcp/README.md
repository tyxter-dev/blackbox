# mcp

`mcp` owns Model Context Protocol integration. It connects MCP servers to the
runtime either as local runtime-managed toolsets or as provider-native remote MCP
declarations.

## Belongs Here

- MCP server specs and safety validation.
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
