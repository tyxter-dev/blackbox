from __future__ import annotations

from pathlib import Path
from typing import Any

from blackbox import AgentRuntime, EventTypes, RuntimeConfig, SkillSpec
from blackbox.tools import ToolResult
from tests.fixtures.scripted_model import ScriptedModelProvider, text_only_turn


def _runtime() -> tuple[AgentRuntime, ScriptedModelProvider]:
    runtime = AgentRuntime()
    scripted = ScriptedModelProvider()
    runtime.registry.register_model(scripted)
    return runtime, scripted


async def test_runtime_run_uses_skill_tools_and_prompt_fragments(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "research"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: research
description: Research with the lookup tool.
tools: [lookup]
---

Always call lookup before finalizing.
""",
        encoding="utf-8",
    )
    runtime, scripted = _runtime()
    runtime.tools.register(
        lambda topic: ToolResult(content=f"lookup:{topic}"),
        name="lookup",
        description="Lookup a topic.",
        parameters={
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    )

    def assert_skill_request(request: Any) -> Any:
        assert [tool["name"] for tool in request.tools] == ["lookup"]
        assert request.controls.instructions is not None
        assert "Always call lookup before finalizing." in request.controls.instructions
        yield from text_only_turn("done")(request)

    scripted.queue(assert_skill_request)

    result = await runtime.run(
        provider="scripted:test",
        input="research apples",
        skills=[SkillSpec.from_directory(skill_dir)],
    )

    assert result.text == "done"
    bundle = next(
        event
        for event in result.events
        if event.type == EventTypes.PROMPT_BUNDLE_CREATED
    )
    assert "skill.research.instructions" in bundle.data["fragment_ids"]


async def test_runtime_config_overrides_can_supply_skills(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "brief"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: brief
description: Keep answers short.
---

Answer in one sentence.
""",
        encoding="utf-8",
    )
    runtime, scripted = _runtime()
    scripted.queue(text_only_turn("short"))

    result = await runtime.run(
        input="summarize",
        config=RuntimeConfig(overrides={"provider": "scripted:test", "skills": [skill_dir]}),
    )

    assert result.text == "short"
    assert "Answer in one sentence." in scripted.calls[0].controls.instructions
