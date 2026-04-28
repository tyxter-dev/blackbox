from importlib import import_module
from typing import Any

__all__ = [
    "ToolCatalog",
    "ToolCatalogEntry",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "ToolRuntime",
    "ToolSession",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ToolCatalog": ("agent_runtime.tools.catalog", "ToolCatalog"),
    "ToolCatalogEntry": ("agent_runtime.tools.catalog", "ToolCatalogEntry"),
    "ToolDefinition": ("agent_runtime.tools.registry", "ToolDefinition"),
    "ToolRegistry": ("agent_runtime.tools.registry", "ToolRegistry"),
    "ToolResult": ("agent_runtime.tools.results", "ToolResult"),
    "ToolRuntime": ("agent_runtime.tools.runtime", "ToolRuntime"),
    "ToolSession": ("agent_runtime.tools.session", "ToolSession"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
