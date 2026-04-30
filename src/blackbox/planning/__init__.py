from importlib import import_module
from typing import Any

__all__ = [
    "CacheSection",
    "DataSourceRef",
    "DynamicToolLoadingSpec",
    "FragmentRequirements",
    "FragmentSelector",
    "PromptBundle",
    "PromptComposer",
    "PromptFragment",
    "PromptFragmentRegistry",
    "PromptMode",
    "PromptParityIssue",
    "PromptParityMode",
    "PromptPlacement",
    "PromptSection",
    "PromptSpec",
    "ResolvedHostedTool",
    "ResolvedMCPToolset",
    "ResolvedRunSpec",
    "ResolvedTool",
    "SelectedPromptFragment",
    "SkippedPromptFragment",
    "assert_prompt_tool_parity",
    "resolved_hosted_tools",
    "validate_prompt_tool_parity",
]

_PROMPT_EXPORTS = {
    "CacheSection",
    "FragmentRequirements",
    "FragmentSelector",
    "PromptBundle",
    "PromptComposer",
    "PromptFragment",
    "PromptFragmentRegistry",
    "PromptMode",
    "PromptParityIssue",
    "PromptParityMode",
    "PromptPlacement",
    "PromptSection",
    "PromptSpec",
    "SelectedPromptFragment",
    "SkippedPromptFragment",
    "assert_prompt_tool_parity",
    "validate_prompt_tool_parity",
}
_RUN_PLAN_EXPORTS = {
    "DataSourceRef",
    "DynamicToolLoadingSpec",
    "ResolvedHostedTool",
    "ResolvedMCPToolset",
    "ResolvedRunSpec",
    "ResolvedTool",
    "resolved_hosted_tools",
}


def __getattr__(name: str) -> Any:
    if name in _PROMPT_EXPORTS:
        value = getattr(import_module("blackbox.planning.prompts"), name)
    elif name in _RUN_PLAN_EXPORTS:
        value = getattr(import_module("blackbox.planning.run_plan"), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
