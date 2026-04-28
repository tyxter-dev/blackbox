from importlib import import_module
from typing import Any

__all__ = [
    "ToolBudget",
    "ToolCatalog",
    "ToolCatalogEntry",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "ToolRuntime",
    "ToolSelection",
    "ToolSession",
    "Toolset",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ToolCatalog": ("agent_runtime.tools.catalog", "ToolCatalog"),
    "ToolCatalogEntry": ("agent_runtime.tools.catalog", "ToolCatalogEntry"),
    "ToolDefinition": ("agent_runtime.tools.registry", "ToolDefinition"),
    "ToolBudget": ("agent_runtime.tools.toolsets", "ToolBudget"),
    "ToolRegistry": ("agent_runtime.tools.registry", "ToolRegistry"),
    "ToolResult": ("agent_runtime.tools.results", "ToolResult"),
    "ToolRuntime": ("agent_runtime.tools.runtime", "ToolRuntime"),
    "ToolSession": ("agent_runtime.tools.session", "ToolSession"),
    "ToolSelection": ("agent_runtime.tools.toolsets", "ToolSelection"),
    "Toolset": ("agent_runtime.tools.toolsets", "Toolset"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
