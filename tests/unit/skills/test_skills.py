from __future__ import annotations

from pathlib import Path

import pytest

from blackbox.core.policy import PolicyDecision, PolicyRequest
from blackbox.mcp import MCPServerSpec
from blackbox.skills import SkillPermissionPolicy, SkillSpec, compile_skills, frontmatter
from blackbox.tools.hosted.specs import WebSearch
from blackbox.tools.registry import ToolRegistry
from blackbox.workspace_agents import ApprovalRequirement, ToolPermission
from blackbox.workspaces import WorkspaceSpec


def test_skill_spec_loads_skill_md_and_exports_markdown(tmp_path: Path) -> None:
    skill_dir = tmp_path / "review-pr"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: review-pr
description: Review pull requests.
version: 0.2.0
tools: [get_diff, post_comment]
hosted_tools:
  - kind: web_search
workspace: { kind: git }
permissions:
  - { ref: post_comment, scopes: [write], approval: { mode: always } }
examples:
  - Review PR #12
---

Check correctness before style.
""",
        encoding="utf-8",
    )

    skill = SkillSpec.from_directory(skill_dir)
    exported = SkillSpec.from_skill_md(skill.to_markdown(), source=str(skill_dir))

    assert skill.name == "review-pr"
    assert skill.tools == ("get_diff", "post_comment")
    assert isinstance(skill.hosted_tools[0], WebSearch)
    assert skill.workspace == WorkspaceSpec(kind="git")
    assert skill.permissions[0].approval.mode == "always"
    assert exported.name == skill.name
    assert exported.instructions == "Check correctness before style."


def test_skill_spec_export_round_trips_without_pyyaml(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import_module = frontmatter.importlib.import_module

    def import_without_yaml(name: str) -> object:
        if name == "yaml":
            raise ModuleNotFoundError(name)
        return original_import_module(name)

    monkeypatch.setattr(frontmatter.importlib, "import_module", import_without_yaml)
    skill = SkillSpec(
        name="review-pr",
        permissions=(
            ToolPermission(
                ref="post_comment",
                scopes=["write"],
                approval=ApprovalRequirement(mode="always"),
            ),
        ),
    )

    exported = SkillSpec.from_skill_md(skill.to_markdown())

    assert exported.permissions == skill.permissions


def test_compile_skills_builds_deterministic_runtime_expansion() -> None:
    skill = SkillSpec(
        name="crm",
        description="Use CRM lookups.",
        instructions="Call lookup_customer before answering.",
        tools=("lookup_customer",),
        hosted_tools=(WebSearch(),),
        mcp_servers=("github",),
        workspace=WorkspaceSpec(kind="local"),
        permissions=(
            ToolPermission(
                ref="lookup_customer",
                scopes=["execute"],
                approval=ApprovalRequirement(mode="always"),
            ),
        ),
    )
    registry = ToolRegistry()
    server = MCPServerSpec(name="github", transport="stdio", command="github-mcp")

    first = compile_skills([skill], registry=registry, mcp_servers={"github": server})
    second = compile_skills([skill], registry=registry, mcp_servers={"github": server})

    assert first.local_tools == ["lookup_customer"]
    assert first.context_flags == ["skill:crm"]
    assert first.prompt_mode == "tool_aware"
    assert first.workspace == WorkspaceSpec(kind="local")
    assert [fragment.id for fragment in first.prompt_fragments] == [
        "skill.crm.summary",
        "skill.crm.instructions",
    ]
    assert [fragment.id for fragment in second.prompt_fragments] == [
        fragment.id for fragment in first.prompt_fragments
    ]
    assert first.mcp_toolsets[0].server.name == "github"


@pytest.mark.asyncio
async def test_skill_permission_policy_requires_approval_at_tool_call() -> None:
    policy = SkillPermissionPolicy(
        "crm",
        [
            ToolPermission(
                ref="lookup_customer",
                scopes=["execute"],
                approval=ApprovalRequirement(mode="always", reason="review lookup"),
            )
        ],
    )

    exposure = await policy.check(
        PolicyRequest(checkpoint="before_tool_exposure", action="lookup_customer")
    )
    call = await policy.check(
        PolicyRequest(checkpoint="before_tool_call", action="lookup_customer")
    )

    assert exposure == PolicyDecision.allow()
    assert call.verdict == "require_approval"
    assert call.reason == "review lookup"
