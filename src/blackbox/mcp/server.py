from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TextIO

MCPToolHandler = Callable[..., Any]
MCPToolAnnotations = dict[str, Any]


@dataclass(slots=True, frozen=True)
class MCPServerResult:
    """Normalized MCP ``tools/call`` result payload."""

    content: list[dict[str, Any]]
    structured_content: Any | None = None
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_protocol(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"content": self.content}
        if self.structured_content is not None:
            payload["structuredContent"] = self.structured_content
        if self.is_error:
            payload["isError"] = True
        if self.metadata:
            payload["_meta"] = self.metadata
        return payload


class MCPToolError(Exception):
    """Business/tool failure returned as an MCP ``isError`` tool result."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.category = category
        self.data = dict(data or {})

    def to_result(self) -> MCPServerResult:
        structured: dict[str, Any] = {
            "error": self.code,
            "message": self.message,
        }
        if self.category is not None:
            structured["category"] = self.category
        if self.data:
            structured["data"] = self.data
        return mcp_error_result(structured)


@dataclass(slots=True, frozen=True)
class MCPServerTool:
    """Tool definition exposed by an ``MCPServer``."""

    name: str
    description: str
    handler: MCPToolHandler
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    input_model: type[Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def descriptor(self) -> dict[str, Any]:
        descriptor: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }
        if self.metadata:
            descriptor["metadata"] = dict(self.metadata)
            annotations = self.metadata.get("annotations")
            if isinstance(annotations, dict):
                descriptor["annotations"] = dict(annotations)
        return descriptor


class MCPServer:
    """Small MCP server-authoring helper compatible with blackbox transports.

    The server speaks blackbox's line-delimited stdio MCP transport. It is meant
    for local adapters around first-party code or external APIs, where the app
    wants Pydantic validation and structured results without hand-writing
    JSON-RPC.
    """

    def __init__(
        self,
        name: str,
        *,
        version: str = "0.1.0",
        instructions: str | None = None,
        protocol_version: str = "2025-11-25",
    ) -> None:
        if not name.strip():
            raise ValueError("MCP server name must be non-empty.")
        self.name = name
        self.version = version
        self.instructions = instructions
        self.protocol_version = protocol_version
        self._tools: dict[str, MCPServerTool] = {}

    @property
    def tools(self) -> tuple[MCPServerTool, ...]:
        return tuple(self._tools.values())

    def tool(
        self,
        name: str | None = None,
        *,
        description: str | None = None,
        input_model: type[Any] | None = None,
        input_schema: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        annotations: Mapping[str, Any] | None = None,
    ) -> Callable[[MCPToolHandler], MCPToolHandler]:
        """Register a callable as an MCP tool.

        When ``input_model`` is a Pydantic v2 model class, its JSON schema is
        exposed through ``tools/list`` and ``tools/call`` arguments are validated
        before the handler runs. The handler receives the validated model
        instance.
        """

        def decorator(handler: MCPToolHandler) -> MCPToolHandler:
            tool_name = name or handler.__name__
            self.register_tool(
                tool_name,
                handler,
                description=description or _description_for(handler),
                input_model=input_model,
                input_schema=input_schema,
                metadata=metadata,
                annotations=annotations,
            )
            return handler

        return decorator

    def register_tool(
        self,
        name: str,
        handler: MCPToolHandler,
        *,
        description: str = "",
        input_model: type[Any] | None = None,
        input_schema: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        annotations: Mapping[str, Any] | None = None,
    ) -> MCPServerTool:
        if not name.strip():
            raise ValueError("MCP tool name must be non-empty.")
        if name in self._tools:
            raise ValueError(f"MCP tool already registered: {name}")
        resolved_metadata = dict(metadata or {})
        if annotations is not None:
            resolved_metadata["annotations"] = dict(annotations)
        if input_schema is not None:
            schema = dict(input_schema)
        elif input_model is not None:
            schema = pydantic_model_schema(input_model)
        else:
            schema = {"type": "object", "properties": {}, "additionalProperties": True}
        definition = MCPServerTool(
            name=name,
            description=description,
            handler=handler,
            input_schema=schema,
            input_model=input_model,
            metadata=resolved_metadata,
        )
        self._tools[name] = definition
        return definition

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.descriptor() for tool in self.tools]

    async def call_tool_result(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> MCPServerResult:
        tool = self._tools.get(name)
        if tool is None:
            return mcp_error_result(
                {
                    "error": "unknown_tool",
                    "message": f"Unknown MCP tool: {name}",
                }
            )
        raw_arguments = dict(arguments or {})
        try:
            result = await self._invoke_tool(tool, raw_arguments)
        except MCPToolError as exc:
            return exc.to_result()
        except Exception as exc:
            return mcp_error_result(
                {
                    "error": "tool_execution_failed",
                    "message": str(exc),
                    "category": type(exc).__name__,
                }
            )
        return normalize_mcp_result(result)

    async def handle_line(self, line: bytes | str) -> dict[str, Any] | None:
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return jsonrpc_error(None, -32700, "Parse error")
        if not isinstance(payload, dict):
            return jsonrpc_error(None, -32600, "Invalid request")
        return await self.handle(payload)

    async def handle(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        request_id = payload.get("id")
        method = payload.get("method")
        if not isinstance(method, str):
            return jsonrpc_error(request_id, -32600, "Invalid request")
        if request_id is None and method.startswith("notifications/"):
            return None
        params = payload.get("params")
        params_dict = dict(params) if isinstance(params, Mapping) else {}

        if method == "initialize":
            result = self._initialize(params_dict)
        elif method == "tools/list":
            result = {"tools": self.list_tools()}
        elif method == "tools/call":
            result = (await self._handle_tool_call(params_dict)).to_protocol()
        else:
            return jsonrpc_error(request_id, -32601, f"Unknown method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def run_stdio(self, *, stdin: Any | None = None, stdout: TextIO | None = None) -> int:
        return asyncio.run(self.run_stdio_async(stdin=stdin, stdout=stdout))

    async def run_stdio_async(
        self,
        *,
        stdin: Any | None = None,
        stdout: TextIO | None = None,
    ) -> int:
        input_stream = stdin if stdin is not None else sys.stdin.buffer
        output_stream = stdout if stdout is not None else sys.stdout
        for line in input_stream:
            if not line.strip():
                continue
            response = await self.handle_line(line)
            if response is None:
                continue
            output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
            output_stream.flush()
        return 0

    async def _handle_tool_call(self, params: Mapping[str, Any]) -> MCPServerResult:
        name = params.get("name")
        if not isinstance(name, str):
            return mcp_error_result(
                {
                    "error": "missing_tool_name",
                    "message": "tools/call requires a tool name.",
                }
            )
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            return mcp_error_result(
                {
                    "error": "invalid_arguments",
                    "message": "Tool arguments must be an object.",
                }
            )
        return await self.call_tool_result(name, arguments)

    async def _invoke_tool(self, tool: MCPServerTool, arguments: dict[str, Any]) -> Any:
        if tool.input_model is not None:
            try:
                validated = validate_pydantic_model(tool.input_model, arguments)
            except Exception as exc:
                raise MCPToolError(
                    "invalid_arguments",
                    "Tool arguments failed validation.",
                    category=type(exc).__name__,
                    data={"errors": validation_errors(exc)},
                ) from exc
            value = tool.handler(validated)
        else:
            value = tool.handler(**arguments)
        if inspect.isawaitable(value):
            return await value
        return value

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        requested = str(params.get("protocolVersion") or self.protocol_version)
        result: dict[str, Any] = {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions is not None:
            result["instructions"] = self.instructions
        return result


def mcp_text_result(
    text: str,
    *,
    structured_content: Any | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> MCPServerResult:
    return MCPServerResult(
        content=[{"type": "text", "text": text}],
        structured_content=structured_content,
        metadata=dict(metadata or {}),
    )


def mcp_json_result(
    data: Any,
    *,
    text: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> MCPServerResult:
    safe_data = json_safe(data)
    rendered = text if text is not None else json.dumps(safe_data, ensure_ascii=False, sort_keys=True)
    return MCPServerResult(
        content=[{"type": "text", "text": rendered}],
        structured_content=safe_data,
        metadata=dict(metadata or {}),
    )


def mcp_error_result(
    error: Mapping[str, Any] | str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> MCPServerResult:
    if isinstance(error, str):
        structured: dict[str, Any] = {"error": "tool_error", "message": error}
    else:
        structured = dict(error)
    rendered = json.dumps(json_safe(structured), ensure_ascii=False, sort_keys=True)
    return MCPServerResult(
        content=[{"type": "text", "text": rendered}],
        structured_content=structured,
        is_error=True,
        metadata=dict(metadata or {}),
    )


def normalize_mcp_result(value: Any) -> MCPServerResult:
    if isinstance(value, MCPServerResult):
        return value
    if isinstance(value, dict) and _looks_like_protocol_result(value):
        return MCPServerResult(
            content=_protocol_content(value),
            structured_content=value.get("structuredContent", value.get("structured_content")),
            is_error=bool(value.get("isError", value.get("is_error", False))),
            metadata=dict(value.get("_meta") or value.get("metadata") or {}),
        )
    if isinstance(value, str):
        return mcp_text_result(value)
    return mcp_json_result(value)


def pydantic_model_schema(model: type[Any]) -> dict[str, Any]:
    schema_fn = getattr(model, "model_json_schema", None)
    if not callable(schema_fn):
        raise TypeError("input_model must be a Pydantic v2 model class.")
    try:
        schema = schema_fn(by_alias=True)
    except TypeError:
        schema = schema_fn()
    if not isinstance(schema, dict):
        raise TypeError("input_model.model_json_schema() returned a non-object schema.")
    return dict(schema)


def validate_pydantic_model(model: type[Any], arguments: Mapping[str, Any]) -> Any:
    validate_fn = getattr(model, "model_validate", None)
    if not callable(validate_fn):
        raise TypeError("input_model must be a Pydantic v2 model class.")
    return validate_fn(dict(arguments))


def validation_errors(exc: Exception) -> Any:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return json_safe(errors())
        except Exception:
            return str(exc)
    return str(exc)


def jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return json_safe(value.model_dump(by_alias=True, exclude_none=True))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _description_for(handler: MCPToolHandler) -> str:
    doc = inspect.getdoc(handler)
    return doc or handler.__name__


def _looks_like_protocol_result(value: Mapping[str, Any]) -> bool:
    return "content" in value and (
        "structuredContent" in value
        or "structured_content" in value
        or "isError" in value
        or "is_error" in value
    )


def _protocol_content(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    content = value.get("content")
    if isinstance(content, list):
        return [dict(item) for item in content if isinstance(item, Mapping)]
    return [{"type": "json", "json": json_safe(dict(value))}]
