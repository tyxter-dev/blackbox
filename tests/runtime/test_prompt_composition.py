from __future__ import annotations

import pytest

from blackbox import (
    AgentRuntime,
    EventTypes,
    FragmentRequirements,
    PromptFragment,
    PromptSpec,
)
from blackbox.core.errors import ConfigurationError
from blackbox.tools import ToolResult
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


async def test_tool_aware_prompt_adds_enabled_tool_fragments_to_model_request() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda: ToolResult(content="ok"),
        name="create_task",
        description="Create a task.",
        prompt_fragments=[
            PromptFragment(
                id="tool.create_task.when_to_use",
                text="Use create_task for follow-ups and internal next actions.",
                source="tool:create_task",
                priority=50,
            )
        ],
    )
    scripted.queue(text_only_turn("done"))

    result = await runtime.run(
        provider="scripted:test",
        input="Make a follow-up.",
        instructions="You are a support agent.",
        tools=["create_task"],
        prompt=PromptSpec(mode="tool_aware"),
    )

    instructions = scripted.calls[0].controls.instructions or ""
    assert "You are a support agent." in instructions
    assert "Use create_task for follow-ups" in instructions
    assert result.metadata["prompt"]["fragment_ids"] == [
        "tool.create_task.when_to_use"
    ]
    assert result.metadata["prompt"]["effective_tool_ids"] == ["create_task"]
    assert result.metadata["prompt"]["parity"] == "pass"
    assert any(event.type == EventTypes.PROMPT_BUNDLE_CREATED for event in result.events)


async def test_prompt_parity_error_blocks_fragments_that_require_disabled_tools() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(lambda: ToolResult(content="task"), name="create_task")
    runtime.tools.register(lambda: ToolResult(content="customer"), name="create_customer")
    runtime.prompt_fragments.register(
        PromptFragment(
            id="crm.customer.lookup_before_create",
            text="Before creating task records, call create_customer.",
            requires=FragmentRequirements(required_tools={"create_customer"}),
            priority=100,
        )
    )

    with pytest.raises(ConfigurationError, match="create_customer"):
        await runtime.run(
            provider="scripted:test",
            input="Create a follow-up.",
            tools=["create_task"],
            prompt=PromptSpec(mode="tool_aware", parity="error"),
        )

    assert scripted.calls == []


async def test_prompt_parity_warns_when_base_prompt_mentions_disabled_tool() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(lambda: ToolResult(content="task"), name="create_task")
    runtime.tools.register(lambda: ToolResult(content="customer"), name="create_customer")
    scripted.queue(text_only_turn("done"))

    result = await runtime.run(
        provider="scripted:test",
        input="Create a follow-up.",
        instructions="Use create_customer before creating a task.",
        tools=["create_task"],
        prompt=PromptSpec(mode="base", parity="warn"),
    )

    prompt = result.metadata["prompt"]
    assert prompt["parity"] == "warn"
    assert prompt["parity_issues"][0]["code"] == "prompt_mentions_disabled_tool"
    assert prompt["parity_issues"][0]["tool_id"] == "create_customer"


async def test_plan_run_builds_prompt_without_executing_model() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda: ToolResult(content="ok"),
        name="query_customers",
        description="Query customers.",
        tags=["crm.customer"],
        prompt_fragments=[
            PromptFragment(
                id="crm.customer.lookup",
                text="Use query_customers before assuming a customer does not exist.",
                source="tool:query_customers",
            )
        ],
    )

    plan = await runtime.plan_run(
        provider="scripted:test",
        input="Find Diego.",
        instructions="You are a CRM agent.",
        tools=["query_customers"],
        prompt=PromptSpec(mode="tool_aware"),
    )

    assert scripted.calls == []
    assert plan.effective_tool_ids == ["query_customers"]
    assert plan.prompt is not None
    assert "Use query_customers" in plan.prompt.instructions
    assert plan.prompt.metadata["fragment_ids"] == ["crm.customer.lookup"]


async def test_section_style_xml_wraps_sections_in_tags() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda: ToolResult(content="ok"),
        name="search_docs",
        description="Search documents.",
        prompt_fragments=[
            PromptFragment(
                id="tool.search_docs.guidance",
                text="Always cite sources when using search_docs.",
                source="tool:search_docs",
                priority=50,
            )
        ],
    )
    scripted.queue(text_only_turn("done"))

    result = await runtime.run(
        provider="scripted:test",
        input="Find something.",
        instructions="You are a research assistant.",
        tools=["search_docs"],
        prompt=PromptSpec(mode="tool_aware", section_style="xml"),
    )

    instructions = scripted.calls[0].controls.instructions or ""
    assert "<instructions>" in instructions
    assert "</instructions>" in instructions
    assert "<tool_search_docs_guidance>" in instructions
    assert "</tool_search_docs_guidance>" in instructions
    assert "You are a research assistant." in instructions
    assert "Always cite sources" in instructions
    assert result.metadata["prompt"]["section_style"] == "xml"


async def test_section_style_markdown_wraps_sections_in_headers() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda: ToolResult(content="ok"),
        name="search_docs",
        description="Search documents.",
        prompt_fragments=[
            PromptFragment(
                id="tool.search_docs.guidance",
                text="Always cite sources when using search_docs.",
                source="tool:search_docs",
                priority=50,
            )
        ],
    )
    scripted.queue(text_only_turn("done"))

    result = await runtime.run(
        provider="scripted:test",
        input="Find something.",
        instructions="You are a research assistant.",
        tools=["search_docs"],
        prompt=PromptSpec(mode="tool_aware", section_style="markdown"),
    )

    instructions = scripted.calls[0].controls.instructions or ""
    assert "# Instructions" in instructions
    assert "# Tool Search Docs Guidance" in instructions
    assert "You are a research assistant." in instructions
    assert "Always cite sources" in instructions
    assert result.metadata["prompt"]["section_style"] == "markdown"


async def test_section_style_none_preserves_raw_content() -> None:
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda: ToolResult(content="ok"),
        name="search_docs",
        description="Search documents.",
        prompt_fragments=[
            PromptFragment(
                id="tool.search_docs.guidance",
                text="Always cite sources when using search_docs.",
                source="tool:search_docs",
                priority=50,
            )
        ],
    )
    scripted.queue(text_only_turn("done"))

    result = await runtime.run(
        provider="scripted:test",
        input="Find something.",
        instructions="You are a research assistant.",
        tools=["search_docs"],
        prompt=PromptSpec(mode="tool_aware", section_style=None),
    )

    instructions = scripted.calls[0].controls.instructions or ""
    assert "<" not in instructions
    assert "# " not in instructions
    assert "You are a research assistant." in instructions
    assert "Always cite sources" in instructions
    assert result.metadata["prompt"]["section_style"] is None


async def test_prompts_facade_returns_the_dry_run_bundle() -> None:
    runtime, _ = _runtime()
    bundle = await runtime.prompts.build(
        provider="scripted:test",
        input="Hello.",
        instructions="Stay concise.",
        prompt=PromptSpec(mode="base"),
    )

    assert bundle.instructions == "Stay concise."
    assert bundle.metadata["parity"] == "pass"
