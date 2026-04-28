from importlib import import_module
from typing import Any

__all__ = [
    "AnthropicManagedAgentProvider",
    "ClaudeCodeAgentProvider",
    "GoogleAgentPlatformProvider",
    "LocalAgentProvider",
    "OpenAICloudAgentProvider",
    "VertexAIAgentEngineProvider",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AnthropicManagedAgentProvider": (
        "agent_runtime.providers.agent_adapters.claude_code",
        "AnthropicManagedAgentProvider",
    ),
    "ClaudeCodeAgentProvider": (
        "agent_runtime.providers.agent_adapters.claude_code",
        "ClaudeCodeAgentProvider",
    ),
    "GoogleAgentPlatformProvider": (
        "agent_runtime.providers.agent_adapters.vertex_agent_engine",
        "GoogleAgentPlatformProvider",
    ),
    "LocalAgentProvider": (
        "agent_runtime.providers.agent_adapters.local",
        "LocalAgentProvider",
    ),
    "OpenAICloudAgentProvider": (
        "agent_runtime.providers.agent_adapters.openai_cloud",
        "OpenAICloudAgentProvider",
    ),
    "VertexAIAgentEngineProvider": (
        "agent_runtime.providers.agent_adapters.vertex_agent_engine",
        "VertexAIAgentEngineProvider",
    ),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
