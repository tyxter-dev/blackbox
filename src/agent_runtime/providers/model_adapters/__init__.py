from importlib import import_module
from typing import Any

__all__ = [
    "AnthropicMessagesProvider",
    "EchoModelProvider",
    "GeminiGenerateContentProvider",
    "OpenAIResponsesProvider",
    "XAIResponsesProvider",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AnthropicMessagesProvider": (
        "agent_runtime.providers.model_adapters.anthropic_messages",
        "AnthropicMessagesProvider",
    ),
    "EchoModelProvider": (
        "agent_runtime.providers.model_adapters.echo",
        "EchoModelProvider",
    ),
    "GeminiGenerateContentProvider": (
        "agent_runtime.providers.model_adapters.gemini_generate_content",
        "GeminiGenerateContentProvider",
    ),
    "OpenAIResponsesProvider": (
        "agent_runtime.providers.model_adapters.openai_responses",
        "OpenAIResponsesProvider",
    ),
    "XAIResponsesProvider": (
        "agent_runtime.providers.model_adapters.xai_responses",
        "XAIResponsesProvider",
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
