"""Compatibility re-exports for prompt planning contracts.

New code should import from :mod:`blackbox.planning.prompts`.
"""

from blackbox.planning.prompts import (
    CacheSection,
    FragmentRequirements,
    FragmentSelector,
    PromptBundle,
    PromptComposer,
    PromptFragment,
    PromptFragmentRegistry,
    PromptMode,
    PromptParityIssue,
    PromptParityMode,
    PromptPlacement,
    PromptSection,
    PromptSpec,
    SectionStyle,
    SelectedPromptFragment,
    SkippedPromptFragment,
    assert_prompt_tool_parity,
    validate_prompt_tool_parity,
)

__all__ = [
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
    "SectionStyle",
    "SelectedPromptFragment",
    "SkippedPromptFragment",
    "assert_prompt_tool_parity",
    "validate_prompt_tool_parity",
]
