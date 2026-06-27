from __future__ import annotations

import dataclasses
import importlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from blackbox.core.errors import ConfigurationError
from blackbox.core.policy import Policy
from blackbox.core.results import OutputSpec
from blackbox.mcp import MCPServerSpec, MCPToolset
from blackbox.planning.prompts import PromptFragment, PromptMode
from blackbox.skills.frontmatter import dump_frontmatter, parse_frontmatter_markdown
from blackbox.tools.hosted.specs import (
    ApplyPatch,
    CodeInterpreter,
    ComputerUse,
    ContainerSpec,
    FileSearch,
    HostedToolRaw,
    HostedToolSpec,
    ImageGeneration,
    Memory,
    RemoteMCP,
    Shell,
    TextEditor,
    ToolNamespace,
    ToolSearch,
    URLContext,
    WebFetch,
    WebSearch,
    hosted_tool_kind,
)
from blackbox.workspaces.spec import WorkspaceKind, WorkspaceSpec

if TYPE_CHECKING:
    from blackbox.workspace_agents.permissions import ToolPermission
    from blackbox.workspace_agents.spec import SkillBundleRef

if TYPE_CHECKING:
    SkillInput: TypeAlias = "SkillSpec | SkillBundleRef | str | Path | Mapping[str, Any]"
else:
    SkillInput: TypeAlias = Any

_KNOWN_FRONTMATTER_KEYS = {
    "context_flag",
    "description",
    "examples",
    "hosted_tools",
    "mcp_servers",
    "metadata",
    "name",
    "output",
    "permissions",
    "policy",
    "source",
    "tools",
    "version",
    "workspace",
}

_HOSTED_TOOL_TYPES: dict[str, type[Any]] = {
    "apply_patch": ApplyPatch,
    "bash": Shell,
    "code_interpreter": CodeInterpreter,
    "computer": ComputerUse,
    "computer_use": ComputerUse,
    "file_search": FileSearch,
    "image_generation": ImageGeneration,
    "memory": Memory,
    "raw": HostedToolRaw,
    "remote_mcp": RemoteMCP,
    "shell": Shell,
    "text_editor": TextEditor,
    "tool_search": ToolSearch,
    "url_context": URLContext,
    "web_fetch": WebFetch,
    "web_search": WebSearch,
}


@dataclass(slots=True, frozen=True)
class SkillSpec:
    """Loaded, parsed, compilable form of a portable skill bundle."""

    name: str
    description: str = ""
    instructions: str = ""
    version: str | None = None
    source: str | None = None
    tools: tuple[str, ...] = ()
    hosted_tools: tuple[HostedToolSpec, ...] = ()
    mcp_servers: tuple[MCPServerSpec | str, ...] = ()
    workspace: WorkspaceSpec | None = None
    context_flag: str | None = None
    permissions: tuple[ToolPermission, ...] = ()
    policy: str | None = None
    output: OutputSpec | None = None
    examples: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_directory(cls, path: str | Path) -> SkillSpec:
        bundle = Path(path).expanduser()
        skill_md = bundle / "SKILL.md"
        if not bundle.is_dir():
            raise ConfigurationError(f"Skill bundle directory does not exist: {bundle}.")
        if not skill_md.is_file():
            raise ConfigurationError(f"Skill bundle is missing SKILL.md: {bundle}.")
        return cls.from_skill_md(skill_md.read_text(encoding="utf-8"), source=str(bundle))

    @classmethod
    def from_skill_md(cls, text: str, *, source: str | None = None) -> SkillSpec:
        frontmatter, body = parse_frontmatter_markdown(text)
        name = _optional_str(frontmatter.get("name"))
        if name is None and source is not None:
            name = Path(source).name
        if name is None or not name.strip():
            raise ConfigurationError("SkillSpec requires a non-empty name.")
        metadata = _coerce_metadata(frontmatter)
        return cls(
            name=name.strip(),
            description=_optional_str(frontmatter.get("description")) or "",
            instructions=body.strip(),
            version=_optional_str(frontmatter.get("version")),
            source=_optional_str(frontmatter.get("source")) or source,
            tools=tuple(_coerce_str_sequence(frontmatter.get("tools"))),
            hosted_tools=tuple(
                _coerce_hosted_tool(item)
                for item in _coerce_sequence(frontmatter.get("hosted_tools"))
            ),
            mcp_servers=tuple(
                _coerce_mcp_server(item)
                for item in _coerce_sequence(frontmatter.get("mcp_servers"))
            ),
            workspace=_coerce_workspace(frontmatter.get("workspace")),
            context_flag=_optional_str(frontmatter.get("context_flag")),
            permissions=tuple(
                _coerce_tool_permission(item)
                for item in _coerce_sequence(frontmatter.get("permissions"))
            ),
            policy=_optional_str(frontmatter.get("policy")),
            output=_coerce_output_spec(frontmatter.get("output")),
            examples=tuple(_coerce_str_sequence(frontmatter.get("examples"))),
            metadata=metadata,
        )

    @classmethod
    def from_bundle_ref(cls, ref: SkillBundleRef) -> SkillSpec:
        if ref.source:
            source = Path(ref.source).expanduser()
            if source.exists():
                spec = cls.from_directory(source)
                metadata = {**dict(spec.metadata), **dict(ref.metadata)}
                return dataclasses.replace(
                    spec,
                    name=ref.name or spec.name,
                    version=ref.version or spec.version,
                    source=str(source),
                    metadata=metadata,
                )
        return cls(
            name=ref.name,
            version=ref.version,
            source=ref.source,
            metadata=dict(ref.metadata),
        )

    def to_markdown(self) -> str:
        payload = self._frontmatter_payload()
        frontmatter = dump_frontmatter(payload)
        if frontmatter:
            return f"---\n{frontmatter}\n---\n\n{self.instructions.strip()}\n"
        return f"{self.instructions.strip()}\n"

    def _frontmatter_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name}
        if self.description:
            payload["description"] = self.description
        if self.version is not None:
            payload["version"] = self.version
        if self.tools:
            payload["tools"] = list(self.tools)
        if self.hosted_tools:
            payload["hosted_tools"] = [_hosted_tool_payload(tool) for tool in self.hosted_tools]
        if self.mcp_servers:
            payload["mcp_servers"] = [
                _mcp_server_payload(server) for server in self.mcp_servers
            ]
        if self.workspace is not None:
            payload["workspace"] = _dataclass_payload(self.workspace)
        if self.context_flag is not None:
            payload["context_flag"] = self.context_flag
        if self.policy is not None:
            payload["policy"] = self.policy
        if self.permissions:
            payload["permissions"] = [_permission_payload(item) for item in self.permissions]
        if self.output is not None:
            payload["output"] = _output_payload(self.output)
        if self.examples:
            payload["examples"] = list(self.examples)
        for key, value in sorted(dict(self.metadata).items()):
            if key not in payload and key not in _KNOWN_FRONTMATTER_KEYS:
                payload[key] = value
        return payload


@dataclass(slots=True)
class SkillExpansion:
    """Runtime primitives produced by compiling active skills."""

    local_tools: list[str] = field(default_factory=list)
    hosted_tools: list[HostedToolSpec] = field(default_factory=list)
    mcp_toolsets: list[MCPToolset] = field(default_factory=list)
    prompt_fragments: list[PromptFragment] = field(default_factory=list)
    context_flags: list[str] = field(default_factory=list)
    prompt_mode: PromptMode | None = None
    output_spec: OutputSpec | None = None
    policy: Policy | None = None
    workspace: WorkspaceSpec | None = None
    tool_permissions: list[ToolPermission] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_skills(values: Sequence[Any] | Any | None) -> list[SkillSpec]:
    if values is None:
        return []
    if (
        isinstance(values, SkillSpec | str | Path | Mapping)
        or _is_skill_bundle_ref(values)
    ):
        raw_values: Sequence[Any] = [values]
    else:
        raw_values = values
    return [_coerce_skill_spec(value) for value in raw_values]


def _coerce_skill_spec(value: Any) -> SkillSpec:
    if isinstance(value, SkillSpec):
        return value
    if _is_skill_bundle_ref(value):
        return SkillSpec.from_bundle_ref(cast("SkillBundleRef", value))
    if isinstance(value, str | Path):
        return SkillSpec.from_directory(value)
    if isinstance(value, Mapping):
        data = dict(value)
        instructions = str(data.pop("instructions", ""))
        if instructions or "name" in data:
            name = _optional_str(data.pop("name", None))
            if name is None or not name.strip():
                raise ConfigurationError("SkillSpec requires a non-empty name.")
            return SkillSpec(
                name=name.strip(),
                description=str(data.pop("description", "")),
                instructions=instructions,
                version=_optional_str(data.pop("version", None)),
                source=_optional_str(data.pop("source", None)),
                tools=tuple(_coerce_str_sequence(data.pop("tools", None))),
                hosted_tools=tuple(
                    _coerce_hosted_tool(item)
                    for item in _coerce_sequence(data.pop("hosted_tools", None))
                ),
                mcp_servers=tuple(
                    _coerce_mcp_server(item)
                    for item in _coerce_sequence(data.pop("mcp_servers", None))
                ),
                workspace=_coerce_workspace(data.pop("workspace", None)),
                context_flag=_optional_str(data.pop("context_flag", None)),
                permissions=tuple(
                    _coerce_tool_permission(item)
                    for item in _coerce_sequence(data.pop("permissions", None))
                ),
                policy=_optional_str(data.pop("policy", None)),
                output=_coerce_output_spec(data.pop("output", None)),
                examples=tuple(_coerce_str_sequence(data.pop("examples", None))),
                metadata=dict(data.pop("metadata", {}), **data),
            )
    raise ConfigurationError(f"Invalid skill entry: {value!r}.")


def _coerce_metadata(frontmatter: Mapping[str, Any]) -> dict[str, Any]:
    metadata = dict(frontmatter.get("metadata") or {})
    for key, value in frontmatter.items():
        if key not in _KNOWN_FRONTMATTER_KEYS:
            metadata[str(key)] = value
    return metadata


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, Sequence):
        return [value]
    return list(value)


def _coerce_str_sequence(value: Any) -> list[str]:
    return [str(item) for item in _coerce_sequence(value) if item is not None]


def _coerce_hosted_tool(value: Any) -> HostedToolSpec:
    if _is_hosted_tool_spec(value):
        return cast(HostedToolSpec, value)
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"Invalid hosted tool spec in SkillSpec: {value!r}.")
    data = dict(value)
    kind = data.pop("kind", data.pop("type", None))
    if kind is None:
        raise ConfigurationError("Hosted tool entries require a 'kind' or 'type'.")
    kind_text = str(kind)
    cls = _HOSTED_TOOL_TYPES.get(kind_text)
    if cls is None:
        raise ConfigurationError(f"Unknown hosted tool kind {kind_text!r}.")
    if cls is Shell and kind_text == "bash":
        data.setdefault("execution", "local")
    if cls is HostedToolRaw:
        payload = data.get("payload")
        if not isinstance(payload, Mapping):
            raise ConfigurationError("HostedToolRaw skill entries require a payload mapping.")
        return HostedToolRaw(payload=dict(payload), provider=_optional_str(data.get("provider")))
    return cast(HostedToolSpec, _dataclass_from_mapping(cls, data))


def _coerce_mcp_server(value: Any) -> MCPServerSpec | str:
    if isinstance(value, MCPServerSpec):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return MCPServerSpec(**dict(value))
    raise ConfigurationError(f"Invalid MCP server entry in SkillSpec: {value!r}.")


def _coerce_workspace(value: Any) -> WorkspaceSpec | None:
    if value is None or isinstance(value, WorkspaceSpec):
        return value
    if isinstance(value, str):
        return WorkspaceSpec(kind=cast(WorkspaceKind, value))
    if isinstance(value, Mapping):
        return WorkspaceSpec(**dict(value))
    raise ConfigurationError(f"Invalid workspace requirement in SkillSpec: {value!r}.")


def _coerce_tool_permission(value: Any) -> ToolPermission:
    from blackbox.workspace_agents.permissions import ApprovalRequirement, ToolPermission

    if isinstance(value, ToolPermission):
        return value
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"Invalid skill permission entry: {value!r}.")
    data = dict(value)
    approval = data.get("approval")
    if isinstance(approval, Mapping):
        data["approval"] = ApprovalRequirement(**dict(approval))
    return ToolPermission(**data)


def _coerce_output_spec(value: Any) -> OutputSpec | None:
    if value is None or isinstance(value, OutputSpec):
        return value
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"Invalid skill output spec: {value!r}.")
    data = dict(value)
    if "schema" in data:
        data["schema"] = _coerce_output_schema_ref(data["schema"])
    return OutputSpec(**data)


def _coerce_output_schema_ref(value: Any) -> type[Any] | dict[str, Any] | None:
    if value is None or isinstance(value, type):
        return value
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        return _import_dotted_ref(value)
    raise ConfigurationError(f"Invalid output schema reference: {value!r}.")


def _import_dotted_ref(value: str) -> type[Any]:
    module_name, sep, attr_path = value.partition(":")
    if not sep:
        module_name, sep, attr_path = value.rpartition(".")
    if not module_name or not attr_path:
        raise ConfigurationError(f"Output schema must be a dotted import path: {value!r}.")
    try:
        obj: Any = importlib.import_module(module_name)
        for attr in attr_path.split("."):
            obj = getattr(obj, attr)
    except (AttributeError, ModuleNotFoundError) as exc:
        raise ConfigurationError(f"Could not import skill output schema {value!r}.") from exc
    if not isinstance(obj, type):
        raise ConfigurationError(f"Skill output schema {value!r} did not resolve to a type.")
    return obj


def _is_hosted_tool_spec(value: Any) -> bool:
    return isinstance(
        value,
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
        | HostedToolRaw,
    )


def _dataclass_from_mapping(cls: type[Any], data: Mapping[str, Any]) -> Any:
    payload = dict(data)
    if cls in {CodeInterpreter, Shell} and isinstance(payload.get("container"), Mapping):
        payload["container"] = ContainerSpec(**dict(payload["container"]))
    if cls is ToolSearch:
        payload["namespaces"] = [
            namespace
            if isinstance(namespace, ToolNamespace)
            else ToolNamespace(**dict(namespace))
            for namespace in _coerce_sequence(payload.get("namespaces"))
        ]
    return cls(**payload)


def _hosted_tool_payload(tool: HostedToolSpec) -> dict[str, Any]:
    payload = _dataclass_payload(tool)
    payload["kind"] = hosted_tool_kind(tool)
    return payload


def _mcp_server_payload(server: MCPServerSpec | str) -> Any:
    if isinstance(server, str):
        return server
    return _drop_empty(server.to_redacted_dict())


def _permission_payload(permission: ToolPermission) -> dict[str, Any]:
    return cast(dict[str, Any], _drop_empty(_dataclass_payload(permission)))


def _output_payload(output: OutputSpec) -> dict[str, Any]:
    payload = _dataclass_payload(output)
    schema = output.schema
    if isinstance(schema, type):
        payload["schema"] = f"{schema.__module__}.{schema.__qualname__}"
    return cast(dict[str, Any], _drop_empty(payload))


def _dataclass_payload(value: Any) -> dict[str, Any]:
    if not dataclasses.is_dataclass(value) or isinstance(value, type):
        raise TypeError(f"Expected dataclass value, got {type(value).__name__}.")
    return cast(dict[str, Any], _drop_empty(dataclasses.asdict(value)))


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_empty(item)
            for key, item in value.items()
            if item is not None and item != [] and item != {} and item != ()
        }
    if isinstance(value, list):
        return [_drop_empty(item) for item in value]
    if isinstance(value, tuple):
        return [_drop_empty(item) for item in value]
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _is_skill_bundle_ref(value: Any) -> bool:
    return (
        type(value).__name__ == "SkillBundleRef"
        and hasattr(value, "name")
        and hasattr(value, "source")
        and hasattr(value, "version")
        and hasattr(value, "metadata")
    )
