from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias

from blackbox.core.errors import UnsupportedFeatureError

HostedExecutionMode: TypeAlias = Literal[
    "provider_server",
    "runtime_client",
    "provider_or_runtime",
]


@dataclass(slots=True, frozen=True)
class ContainerSpec:
    """Provider container configuration for hosted code/shell tools."""

    type: Literal["auto", "container_id", "container_auto", "container_reference"] = "auto"
    container_id: str | None = None
    memory_limit: str | None = None
    file_ids: list[str] = field(default_factory=list)
    network_policy: dict[str, Any] | None = None
    expires_after: dict[str, Any] | None = None


@dataclass(slots=True, frozen=True)
class WebSearch:
    """Provider-native web search tool configuration.

    This describes a search capability the provider may execute on its own
    servers. Domain limits such as allowed/blocked domains are requests to the
    adapter; adapters should raise when the provider cannot enforce an
    explicitly requested restriction.
    """

    search_context_size: str | None = None
    user_location: dict[str, Any] | None = None
    max_uses: int | None = None
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    version: str | None = None


@dataclass(slots=True, frozen=True)
class WebFetch:
    """Provider-native URL fetch tool configuration.

    ``urls`` can pre-authorize known pages for a turn, while domain lists
    constrain any provider-side fetch behavior when the provider supports
    those controls.
    """

    urls: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    blocked_domains: list[str] = field(default_factory=list)
    version: str | None = None


@dataclass(slots=True, frozen=True)
class URLContext:
    """Static URL context attached to a model request.

    Unlike ``WebFetch``, this is context selection rather than a general fetch
    capability: the model/provider receives or resolves the listed URLs as
    bounded reference material for the current turn.
    """

    urls: list[str]
    max_pages: int | None = None


@dataclass(slots=True, frozen=True)
class FileSearch:
    """Provider-native retrieval over configured file/vector corpora.

    Vector stores and corpus IDs identify provider-managed indexes. ``filters``
    and ``include_results`` are mapped only when a provider exposes comparable
    retrieval controls. For small OpenAI demos, use
    ``blackbox.temporary_openai_file_search(...)`` to create a ready
    ``FileSearch`` spec from inline documents.
    """

    vector_store_ids: list[str] = field(default_factory=list)
    max_num_results: int | None = None
    filters: dict[str, Any] | None = None
    include_results: bool = False
    corpus_ids: list[str] = field(default_factory=list)
    raw_config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class CodeInterpreter:
    """Provider-hosted code execution tool.

    The provider owns execution when available. Container fields describe the
    desired execution environment and attached files; adapters should not fall
    back to local execution unless the caller explicitly selected a local tool.
    """

    container_id: str | None = None
    container: ContainerSpec | None = None
    file_ids: list[str] = field(default_factory=list)
    memory_limit: str | None = None
    version: str | None = None
    include_generated_files: bool = True


@dataclass(slots=True, frozen=True)
class Shell:
    """Provider shell tool. Can be hosted or runtime-executed."""

    execution: Literal["hosted", "local"] = "hosted"
    container: ContainerSpec | None = None
    timeout_ms: int | None = None
    max_output_length: int | None = None
    require_approval: bool = True
    allowed_commands: list[str] = field(default_factory=list)
    blocked_commands: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class ApplyPatch:
    """Provider-native patch proposal tool, applied by runtime/workspace."""

    workspace_id: str | None = None
    require_approval: bool = True
    allowed_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)
    max_patch_bytes: int | None = None
    dry_run: bool = False


@dataclass(slots=True, frozen=True)
class ComputerUse:
    """Provider-native computer/browser action tool."""

    harness: str | None = None
    environment: Literal["browser", "desktop", "vm", "custom"] = "browser"
    display_width: int | None = None
    display_height: int | None = None
    screenshot_detail: Literal["original", "high", "low", "auto"] = "original"
    max_actions_per_turn: int | None = None
    require_approval_for: list[str] = field(
        default_factory=lambda: ["submit", "purchase", "delete"]
    )
    action_allowlist: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class TextEditor:
    """Anthropic-schema client text editor tool."""

    name: str = "str_replace_based_edit_tool"
    version: str | None = None
    max_characters: int | None = None


@dataclass(slots=True, frozen=True)
class Memory:
    """Anthropic-schema client memory tool."""

    name: str = "memory"
    version: str | None = None


@dataclass(slots=True, frozen=True)
class ImageGeneration:
    """Provider-native image generation or editing tool configuration."""

    size: str | None = None
    quality: str | None = None
    format: str | None = None
    background: str | None = None
    action: Literal["auto", "generate", "edit"] = "auto"
    include_artifact: bool = True


@dataclass(slots=True, frozen=True)
class ToolNamespace:
    """Searchable namespace exposed to provider or runtime tool search."""

    name: str
    description: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class ToolSearch:
    """Deferred tool discovery configuration.

    Provider strategy asks the model API to search tool namespaces natively.
    Runtime catalog strategy exposes a client-side catalog so the runtime can
    load concrete tools after the model selects a relevant capability.
    """

    namespaces: list[ToolNamespace] = field(default_factory=list)
    strategy: Literal["provider", "runtime_catalog"] = "provider"
    max_results: int | None = None
    defer_loading_default: bool = True
    variant: Literal["auto", "regex", "bm25"] = "auto"


MCPApprovalPolicy: TypeAlias = Literal["always", "never"] | dict[str, Any]


@dataclass(slots=True, frozen=True, repr=False)
class RemoteMCP:
    """Provider-native remote MCP server declaration.

    This is the model-visible MCP server configuration for providers that can
    connect to remote MCP directly. ``allowed_tools``/``denied_tools`` scope the
    provider-visible catalog, ``require_approval`` requests provider-side
    approval behavior, and ``cache_control`` carries provider-specific prompt
    cache hints for stable server/tool descriptions.
    """

    server_label: str
    server_url: str | None = None
    connector_id: str | None = None
    server_description: str | None = None
    authorization: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] | dict[str, Any] | None = None
    denied_tools: list[str] | None = None
    require_approval: MCPApprovalPolicy | None = None
    defer_loading: bool | None = None
    tool_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    cache_control: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        values = {
            "server_label": self.server_label,
            "server_url": self.server_url,
            "connector_id": self.connector_id,
            "server_description": self.server_description,
            "authorization": "<redacted>" if self.authorization is not None else None,
            "headers": _redact_header_mapping(self.headers),
            "allowed_tools": self.allowed_tools,
            "denied_tools": self.denied_tools,
            "require_approval": self.require_approval,
            "defer_loading": self.defer_loading,
            "tool_configs": self.tool_configs,
            "cache_control": self.cache_control,
            "extra": self.extra,
        }
        args = ", ".join(f"{key}={value!r}" for key, value in values.items())
        return f"RemoteMCP({args})"


@dataclass(slots=True, frozen=True)
class HostedToolRaw:
    """Provider-specific hosted tool payload passed through unchanged."""

    payload: dict[str, Any]
    provider: str | None = None


class HostedToolHandler(Protocol):
    """Runtime-side executor for hosted tools that need client handling."""

    @property
    def hosted_tool_type(self) -> str: ...

    async def handle(self, call: Any, context: Any) -> Any:
        """Execute a hosted tool call with provider-specific call and context objects."""
        ...


@dataclass(slots=True)
class HostedToolHandlers:
    """Handlers the runtime can use for client-executed hosted tools."""

    shell: HostedToolHandler | None = None
    apply_patch: HostedToolHandler | None = None
    computer: HostedToolHandler | None = None
    text_editor: HostedToolHandler | None = None
    memory: HostedToolHandler | None = None
    custom: dict[str, HostedToolHandler] = field(default_factory=dict)


HostedToolSpec: TypeAlias = (
    WebSearch
    | WebFetch
    | URLContext
    | FileSearch
    | CodeInterpreter
    | Shell
    | ApplyPatch
    | ComputerUse
    | TextEditor
    | Memory
    | ImageGeneration
    | ToolSearch
    | RemoteMCP
    | HostedToolRaw
)


def hosted_tool_kind(spec: HostedToolSpec) -> str:
    """Return the provider-neutral routing kind for a hosted tool spec."""

    if isinstance(spec, WebSearch):
        return "web_search"
    if isinstance(spec, WebFetch):
        return "web_fetch"
    if isinstance(spec, URLContext):
        return "url_context"
    if isinstance(spec, FileSearch):
        return "file_search"
    if isinstance(spec, CodeInterpreter):
        return "code_interpreter"
    if isinstance(spec, Shell):
        return "bash" if spec.execution == "local" else "shell"
    if isinstance(spec, ApplyPatch):
        return "apply_patch"
    if isinstance(spec, ComputerUse):
        return "computer_use"
    if isinstance(spec, TextEditor):
        return "text_editor"
    if isinstance(spec, Memory):
        return "memory"
    if isinstance(spec, ImageGeneration):
        return "image_generation"
    if isinstance(spec, ToolSearch):
        return "tool_search"
    if isinstance(spec, RemoteMCP):
        return "remote_mcp"
    if isinstance(spec, HostedToolRaw):
        return "raw"
    raise TypeError(f"Unknown hosted tool spec: {type(spec).__name__}")


def _redact_header_mapping(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        redacted[key] = "<redacted>" if _is_sensitive_header(key) else value
    return redacted


def _is_sensitive_header(key: str) -> bool:
    normalized = key.lower().replace("_", "-")
    return any(
        marker in normalized
        for marker in ("authorization", "token", "secret", "api-key")
    )


def to_openai_tool(spec: HostedToolSpec) -> dict[str, Any]:
    """Convert a hosted tool spec to the OpenAI tool payload shape."""

    if isinstance(spec, WebSearch):
        payload: dict[str, Any] = {"type": "web_search"}
        if spec.search_context_size is not None:
            payload["search_context_size"] = spec.search_context_size
        if spec.user_location is not None:
            payload["user_location"] = dict(spec.user_location)
        return payload
    if isinstance(spec, WebFetch):
        raise UnsupportedFeatureError("OpenAI hosted tool mapping is not implemented for WebFetch.")
    if isinstance(spec, URLContext):
        raise UnsupportedFeatureError(
            "OpenAI hosted tool mapping is not implemented for URLContext."
        )
    if isinstance(spec, FileSearch):
        payload = {"type": "file_search", "vector_store_ids": list(spec.vector_store_ids)}
        if spec.max_num_results is not None:
            payload["max_num_results"] = spec.max_num_results
        if spec.filters is not None:
            payload["filters"] = dict(spec.filters)
        return payload
    if isinstance(spec, CodeInterpreter):
        payload = {"type": "code_interpreter"}
        container = _container_payload(spec.container)
        if container is not None:
            payload["container"] = container
        elif spec.container_id is not None:
            payload["container"] = {"type": "container_id", "container_id": spec.container_id}
        if spec.file_ids:
            payload["file_ids"] = list(spec.file_ids)
        if spec.memory_limit is not None:
            payload["memory_limit"] = spec.memory_limit
        return payload
    if isinstance(spec, Shell):
        payload = {"type": "shell", "execution": spec.execution}
        container = _container_payload(spec.container)
        if container is not None:
            payload["container"] = container
        if spec.timeout_ms is not None:
            payload["timeout_ms"] = spec.timeout_ms
        if spec.max_output_length is not None:
            payload["max_output_length"] = spec.max_output_length
        return payload
    if isinstance(spec, ApplyPatch):
        payload = {"type": "apply_patch"}
        if spec.workspace_id is not None:
            payload["workspace_id"] = spec.workspace_id
        return payload
    if isinstance(spec, ComputerUse):
        payload = {"type": "computer", "environment": spec.environment}
        if spec.display_width is not None:
            payload["display_width"] = spec.display_width
        if spec.display_height is not None:
            payload["display_height"] = spec.display_height
        return payload
    if isinstance(spec, TextEditor):
        raise UnsupportedFeatureError(
            "OpenAI hosted tool mapping is not implemented for TextEditor."
        )
    if isinstance(spec, Memory):
        raise UnsupportedFeatureError("OpenAI hosted tool mapping is not implemented for Memory.")
    if isinstance(spec, ImageGeneration):
        payload = {"type": "image_generation"}
        if spec.size is not None:
            payload["size"] = spec.size
        if spec.quality is not None:
            payload["quality"] = spec.quality
        if spec.format is not None:
            payload["format"] = spec.format
        if spec.background is not None:
            payload["background"] = spec.background
        return payload
    if isinstance(spec, ToolSearch):
        payload = {"type": "tool_search"}
        if spec.max_results is not None:
            payload["max_results"] = spec.max_results
        return payload
    if isinstance(spec, RemoteMCP):
        if spec.denied_tools:
            raise UnsupportedFeatureError(
                "OpenAI RemoteMCP mapping supports allowed_tools, but not denied_tools."
            )
        if bool(spec.server_url) == bool(spec.connector_id):
            raise UnsupportedFeatureError(
                "OpenAI RemoteMCP mapping requires exactly one of server_url or connector_id."
            )
        payload = {
            "type": "mcp",
            "server_label": spec.server_label,
        }
        if spec.server_url is not None:
            payload["server_url"] = spec.server_url
        if spec.connector_id is not None:
            payload["connector_id"] = spec.connector_id
        if spec.server_description is not None:
            payload["server_description"] = spec.server_description
        if spec.authorization is not None:
            payload["authorization"] = spec.authorization
        if spec.headers:
            if spec.authorization is not None and any(
                key.lower() == "authorization" for key in spec.headers
            ):
                raise UnsupportedFeatureError(
                    "OpenAI RemoteMCP mapping cannot set both authorization "
                    "and headers.Authorization."
                )
            payload["headers"] = dict(spec.headers)
        if spec.allowed_tools is not None:
            payload["allowed_tools"] = (
                list(spec.allowed_tools)
                if isinstance(spec.allowed_tools, list)
                else dict(spec.allowed_tools)
            )
        if spec.require_approval is not None:
            payload["require_approval"] = spec.require_approval
        payload.update(spec.extra)
        return payload
    return dict(spec.payload)


def to_gemini_tool(spec: HostedToolSpec) -> dict[str, Any]:
    """Convert a hosted tool spec to the Gemini tool payload shape."""

    if isinstance(spec, WebSearch):
        return {"google_search": {}}
    if isinstance(spec, CodeInterpreter):
        return {"code_execution": {}}
    if isinstance(spec, URLContext):
        url_payload: dict[str, Any] = {"urls": list(spec.urls)}
        if spec.max_pages is not None:
            url_payload["max_pages"] = spec.max_pages
        return {"url_context": url_payload}
    if isinstance(spec, FileSearch):
        if spec.raw_config:
            return {"file_search": dict(spec.raw_config)}
        if spec.corpus_ids:
            return {"file_search": {"corpus_ids": list(spec.corpus_ids)}}
        raise UnsupportedFeatureError(
            "Gemini FileSearch requires corpus_ids or raw_config; OpenAI vector_store_ids "
            "cannot be mapped."
        )
    if isinstance(spec, ComputerUse):
        computer_payload: dict[str, Any] = {}
        if spec.environment:
            computer_payload["environment"] = spec.environment
        return {"computer_use": computer_payload}
    if isinstance(spec, TextEditor):
        raise UnsupportedFeatureError(
            "Gemini hosted tool mapping is not implemented for TextEditor."
        )
    if isinstance(spec, Memory):
        raise UnsupportedFeatureError("Gemini hosted tool mapping is not implemented for Memory.")
    if isinstance(spec, HostedToolRaw):
        return dict(spec.payload)
    raise UnsupportedFeatureError(
        f"Gemini hosted tool mapping is not implemented for {type(spec).__name__}."
    )


def to_anthropic_tool(spec: HostedToolSpec, *, model: str | None = None) -> dict[str, Any]:
    """Convert a hosted tool spec to the Anthropic tool payload shape."""

    if isinstance(spec, WebSearch):
        payload: dict[str, Any] = {
            "type": spec.version or _anthropic_web_search_version(model),
            "name": "web_search",
        }
        if spec.max_uses is not None:
            payload["max_uses"] = spec.max_uses
        if spec.allowed_domains:
            payload["allowed_domains"] = list(spec.allowed_domains)
        if spec.blocked_domains:
            payload["blocked_domains"] = list(spec.blocked_domains)
        return payload
    if isinstance(spec, WebFetch):
        fetch_payload: dict[str, Any] = {
            "type": spec.version or "web_fetch_20260209",
            "name": "web_fetch",
        }
        if spec.urls:
            fetch_payload["urls"] = list(spec.urls)
        if spec.allowed_domains:
            fetch_payload["allowed_domains"] = list(spec.allowed_domains)
        if spec.blocked_domains:
            fetch_payload["blocked_domains"] = list(spec.blocked_domains)
        return fetch_payload
    if isinstance(spec, CodeInterpreter):
        return {
            "type": spec.version or "code_execution_20260120",
            "name": "code_execution",
        }
    if isinstance(spec, ToolSearch):
        variant = "regex" if spec.variant == "auto" else spec.variant
        return {
            "type": f"tool_search_tool_{variant}_20251119",
            "name": "tool_search",
        }
    if isinstance(spec, Shell) and spec.execution == "local":
        return {"type": "bash_20250124", "name": "bash"}
    if isinstance(spec, ComputerUse):
        computer_payload: dict[str, Any] = {
            "type": "computer_20250124",
            "name": "computer",
        }
        if spec.display_width is not None:
            computer_payload["display_width_px"] = spec.display_width
        if spec.display_height is not None:
            computer_payload["display_height_px"] = spec.display_height
        return computer_payload
    if isinstance(spec, TextEditor):
        editor_payload: dict[str, Any] = {
            "type": spec.version or _anthropic_text_editor_version(model),
            "name": spec.name,
        }
        if spec.max_characters is not None:
            editor_payload["max_characters"] = spec.max_characters
        return editor_payload
    if isinstance(spec, Memory):
        return {
            "type": spec.version or "memory_20250818",
            "name": spec.name,
        }
    if isinstance(spec, RemoteMCP):
        if spec.headers:
            raise UnsupportedFeatureError(
                "Anthropic RemoteMCP mapping does not support custom headers."
            )
        if spec.server_url is None:
            raise UnsupportedFeatureError("Anthropic RemoteMCP mapping requires server_url.")
        mcp_payload: dict[str, Any] = {
            "type": "mcp_toolset",
            "mcp_server_name": spec.server_label,
        }
        default_config: dict[str, Any] = {}
        configs: dict[str, dict[str, Any]] = {
            name: dict(config) for name, config in spec.tool_configs.items()
        }
        if isinstance(spec.allowed_tools, list) and spec.allowed_tools:
            default_config["enabled"] = False
            for name in spec.allowed_tools:
                configs.setdefault(name, {})["enabled"] = True
        if spec.denied_tools:
            for name in spec.denied_tools:
                configs.setdefault(name, {})["enabled"] = False
        if spec.defer_loading is not None:
            default_config["defer_loading"] = spec.defer_loading
        if default_config:
            mcp_payload["default_config"] = default_config
        if configs:
            mcp_payload["configs"] = configs
        if spec.cache_control is not None:
            mcp_payload["cache_control"] = dict(spec.cache_control)
        return mcp_payload
    if isinstance(spec, HostedToolRaw):
        return dict(spec.payload)
    raise UnsupportedFeatureError(
        f"Anthropic hosted tool mapping is not implemented for {type(spec).__name__}."
    )


def _anthropic_web_search_version(model: str | None) -> str:
    if model is None:
        return "web_search_20260209"
    normalized = model.lower().replace("_", "-")
    if "sonnet-4-6" in normalized or "opus-4-6" in normalized or "4.6" in normalized:
        return "web_search_20260209"
    return "web_search_20250305"


def _anthropic_text_editor_version(model: str | None) -> str:
    if model is None:
        return "text_editor_20250728"
    normalized = model.lower().replace("_", "-")
    if "claude-4" in normalized or "-4-" in normalized:
        return "text_editor_20250728"
    return "text_editor_20250124"


def anthropic_mcp_servers(specs: list[HostedToolSpec]) -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, RemoteMCP):
            continue
        if spec.headers:
            raise UnsupportedFeatureError(
                "Anthropic RemoteMCP mapping does not support custom headers."
            )
        if spec.server_url is None:
            raise UnsupportedFeatureError("Anthropic RemoteMCP mapping requires server_url.")
        server: dict[str, Any] = {
            "type": "url",
            "url": spec.server_url,
            "name": spec.server_label,
        }
        if spec.authorization is not None:
            server["authorization_token"] = spec.authorization
        servers.append(server)
    return servers


def anthropic_beta_values(specs: list[HostedToolSpec]) -> list[str]:
    values: list[str] = []
    if any(isinstance(spec, RemoteMCP) for spec in specs):
        values.append("mcp-client-2025-11-20")
    return values


def to_raw_hosted_tool(spec: HostedToolSpec, *, provider: str) -> dict[str, Any]:
    if isinstance(spec, HostedToolRaw):
        return dict(spec.payload)
    raise UnsupportedFeatureError(
        f"{provider} hosted tool mapping is not implemented for {type(spec).__name__}."
    )


def openai_include_values(specs: list[HostedToolSpec]) -> list[str]:
    include: list[str] = []
    if any(isinstance(spec, FileSearch) and spec.include_results for spec in specs):
        include.append("file_search_call.results")
    return include


def openai_namespace_tools(specs: list[HostedToolSpec]) -> list[dict[str, Any]]:
    namespace_tools: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, ToolSearch):
            continue
        for namespace in spec.namespaces:
            for tool in namespace.tools:
                payload = dict(tool)
                payload.setdefault("namespace", namespace.name)
                if namespace.description is not None:
                    payload.setdefault("namespace_description", namespace.description)
                payload.setdefault("defer_loading", spec.defer_loading_default)
                namespace_tools.append(payload)
    return namespace_tools


def anthropic_deferred_tools(specs: list[HostedToolSpec]) -> list[dict[str, Any]]:
    deferred: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, ToolSearch):
            continue
        for namespace in spec.namespaces:
            for tool in namespace.tools:
                payload = dict(tool)
                payload.setdefault("defer_loading", spec.defer_loading_default)
                if namespace.description is not None:
                    payload.setdefault("description", namespace.description)
                deferred.append(payload)
    return deferred


def _container_payload(container: ContainerSpec | None) -> dict[str, Any] | None:
    if container is None:
        return None
    payload: dict[str, Any] = {"type": container.type}
    if container.container_id is not None:
        payload["container_id"] = container.container_id
    if container.memory_limit is not None:
        payload["memory_limit"] = container.memory_limit
    if container.file_ids:
        payload["file_ids"] = list(container.file_ids)
    if container.network_policy is not None:
        payload["network_policy"] = dict(container.network_policy)
    if container.expires_after is not None:
        payload["expires_after"] = dict(container.expires_after)
    return payload
