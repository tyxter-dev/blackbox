from importlib import import_module
from typing import Any

__all__ = [
    "ToolBudget",
    "ToolCandidate",
    "ToolCatalog",
    "ToolCatalogEntry",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "ToolRoutingSpec",
    "ToolRuntime",
    "ToolSelection",
    "ToolSession",
    "Toolset",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ToolCatalog": ("blackbox.tools.catalog", "ToolCatalog"),
    "ToolCatalogEntry": ("blackbox.tools.catalog", "ToolCatalogEntry"),
    "ToolCandidate": ("blackbox.tools.routing", "ToolCandidate"),
    "ToolDefinition": ("blackbox.tools.registry", "ToolDefinition"),
    "ToolBudget": ("blackbox.tools.toolsets", "ToolBudget"),
    "ToolRoutingSpec": ("blackbox.tools.routing", "ToolRoutingSpec"),
    "ToolRegistry": ("blackbox.tools.registry", "ToolRegistry"),
    "ToolResult": ("blackbox.tools.results", "ToolResult"),
    "ToolRuntime": ("blackbox.tools.runtime", "ToolRuntime"),
    "ToolSession": ("blackbox.tools.session", "ToolSession"),
    "ToolSelection": ("blackbox.tools.toolsets", "ToolSelection"),
    "Toolset": ("blackbox.tools.toolsets", "Toolset"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
