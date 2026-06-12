"""On-disk workspace agent package format: save/load/pack/unpack/install."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from blackbox.workspace_agents import (
    INSTRUCTIONS_FILENAME,
    MANIFEST_FILENAME,
    InMemoryWorkspaceAgentRegistry,
    ScheduleSpec,
    ScheduleTrigger,
    SkillBundleRef,
    WorkspaceAgentPackageError,
    WorkspaceAgentSpec,
    install_workspace_agent_package,
    load_workspace_agent_package,
    pack_workspace_agent_package,
    save_workspace_agent_package,
    unpack_workspace_agent_package,
)
from blackbox.workspace_agents.permissions import ConnectorSpec, ToolPermission


def make_skill_dir(tmp_path: Path) -> Path:
    skill_dir = tmp_path / "skill_src" / "reporting"
    (skill_dir / "templates").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Reporting skill\n", encoding="utf-8")
    (skill_dir / "templates" / "weekly.md").write_text("template body", encoding="utf-8")
    return skill_dir


def make_spec(skill_dir: Path | None = None) -> WorkspaceAgentSpec:
    skills = []
    if skill_dir is not None:
        skills.append(SkillBundleRef(name="reporting", source=str(skill_dir), version="1.0"))
    return WorkspaceAgentSpec(
        name="weekly-reporter",
        instructions="Produce the weekly report.\n\nBe concise.",
        model_provider="openai",
        model="gpt-5",
        tools=["fetch_metrics"],
        connectors=[ConnectorSpec(name="crm", kind="http_api")],
        permissions=[ToolPermission(ref="fetch_metrics")],
        schedules=[
            ScheduleSpec(name="weekly", trigger=ScheduleTrigger(kind="cron", expression="0 9 * * 1"))
        ],
        skills=skills,
    )


def test_save_writes_manifest_instructions_and_skills(tmp_path: Path) -> None:
    skill_dir = make_skill_dir(tmp_path)
    spec = make_spec(skill_dir)
    root = save_workspace_agent_package(spec, tmp_path / "pkg")

    manifest = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["format"] == "blackbox/workspace-agent"
    assert manifest["format_version"] == 1
    assert manifest["spec"]["name"] == "weekly-reporter"
    assert "instructions" not in manifest["spec"]

    instructions = (root / INSTRUCTIONS_FILENAME).read_text(encoding="utf-8")
    assert instructions == "Produce the weekly report.\n\nBe concise."

    assert (root / "skills" / "reporting" / "SKILL.md").is_file()
    assert (root / "skills" / "reporting" / "templates" / "weekly.md").is_file()
    assert manifest["spec"]["skills"][0]["source"] == "skills/reporting"


def test_round_trip_preserves_spec(tmp_path: Path) -> None:
    skill_dir = make_skill_dir(tmp_path)
    spec = make_spec(skill_dir)
    root = save_workspace_agent_package(spec, tmp_path / "pkg")

    loaded = load_workspace_agent_package(root)

    assert loaded.id == spec.id
    assert loaded.name == spec.name
    assert loaded.instructions == spec.instructions
    assert loaded.model_provider == "openai"
    assert loaded.tools == ["fetch_metrics"]
    assert loaded.connectors[0].name == "crm"
    assert loaded.permissions[0].ref == "fetch_metrics"
    assert loaded.schedules[0].trigger.expression == "0 9 * * 1"
    # Embedded skill source resolves to an absolute, existing path.
    skill = loaded.skills[0]
    assert skill.version == "1.0"
    skill_path = Path(skill.source or "")
    assert skill_path.is_absolute()
    assert (skill_path / "SKILL.md").is_file()


def test_instructions_file_edit_wins_over_manifest(tmp_path: Path) -> None:
    root = save_workspace_agent_package(make_spec(), tmp_path / "pkg")
    (root / INSTRUCTIONS_FILENAME).write_text("Edited by hand.", encoding="utf-8")
    assert load_workspace_agent_package(root).instructions == "Edited by hand."


def test_non_local_skill_sources_are_left_verbatim(tmp_path: Path) -> None:
    spec = WorkspaceAgentSpec(
        name="agent",
        skills=[SkillBundleRef(name="remote", source="https://example.com/skills/remote.zip")],
    )
    root = save_workspace_agent_package(spec, tmp_path / "pkg")
    loaded = load_workspace_agent_package(root)
    assert loaded.skills[0].source == "https://example.com/skills/remote.zip"
    assert not (root / "skills").exists()


def test_save_refuses_overwrite_without_flag(tmp_path: Path) -> None:
    save_workspace_agent_package(make_spec(), tmp_path / "pkg")
    with pytest.raises(WorkspaceAgentPackageError, match="overwrite"):
        save_workspace_agent_package(make_spec(), tmp_path / "pkg")
    save_workspace_agent_package(make_spec(), tmp_path / "pkg", overwrite=True)


def test_load_rejects_missing_wrong_or_newer_packages(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceAgentPackageError, match="not an agent package"):
        load_workspace_agent_package(tmp_path / "nope")

    root = tmp_path / "bad_format"
    root.mkdir()
    (root / MANIFEST_FILENAME).write_text(
        json.dumps({"format": "other/thing", "format_version": 1, "spec": {"name": "x"}}),
        encoding="utf-8",
    )
    with pytest.raises(WorkspaceAgentPackageError, match="Unsupported package format"):
        load_workspace_agent_package(root)

    root2 = tmp_path / "newer"
    root2.mkdir()
    (root2 / MANIFEST_FILENAME).write_text(
        json.dumps(
            {"format": "blackbox/workspace-agent", "format_version": 99, "spec": {"name": "x"}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(WorkspaceAgentPackageError, match="newer than the supported"):
        load_workspace_agent_package(root2)


def test_load_rejects_missing_embedded_skill(tmp_path: Path) -> None:
    skill_dir = make_skill_dir(tmp_path)
    root = save_workspace_agent_package(make_spec(skill_dir), tmp_path / "pkg")
    (root / "skills" / "reporting" / "SKILL.md").unlink()
    # Removing one file is fine (directory still exists)...
    load_workspace_agent_package(root)
    # ...removing the whole bundle is not.
    shutil.rmtree(root / "skills" / "reporting")
    with pytest.raises(WorkspaceAgentPackageError, match="does not exist"):
        load_workspace_agent_package(root)


def test_pack_and_unpack_round_trip(tmp_path: Path) -> None:
    skill_dir = make_skill_dir(tmp_path)
    spec = make_spec(skill_dir)
    root = save_workspace_agent_package(spec, tmp_path / "pkg")
    archive = pack_workspace_agent_package(root, tmp_path / "dist" / "agent.zip")
    assert archive.is_file()

    dest = unpack_workspace_agent_package(archive, tmp_path / "installed")
    loaded = load_workspace_agent_package(dest)
    assert loaded.id == spec.id
    assert loaded.instructions == spec.instructions
    assert Path(loaded.skills[0].source or "").is_dir()


def test_pack_requires_saved_package(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceAgentPackageError, match="save the package first"):
        pack_workspace_agent_package(tmp_path, tmp_path / "agent.zip")


def test_unpack_rejects_zip_slip(tmp_path: Path) -> None:
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../outside.txt", "escape")
    with pytest.raises(WorkspaceAgentPackageError, match="escapes the destination"):
        unpack_workspace_agent_package(evil, tmp_path / "dest")

    absolute = tmp_path / "absolute.zip"
    with zipfile.ZipFile(absolute, "w") as zf:
        zf.writestr("/etc/passwd", "root")
    with pytest.raises(WorkspaceAgentPackageError, match="absolute path"):
        unpack_workspace_agent_package(absolute, tmp_path / "dest")


async def test_install_from_directory_and_archive(tmp_path: Path) -> None:
    skill_dir = make_skill_dir(tmp_path)
    spec = make_spec(skill_dir)
    root = save_workspace_agent_package(spec, tmp_path / "pkg")
    registry = InMemoryWorkspaceAgentRegistry()

    installed = await install_workspace_agent_package(root, registry)
    assert installed.id == spec.id
    assert (await registry.get(spec.id)).name == "weekly-reporter"

    archive = pack_workspace_agent_package(root, tmp_path / "agent.zip")
    registry2 = InMemoryWorkspaceAgentRegistry()
    with pytest.raises(WorkspaceAgentPackageError, match="unpack_dir"):
        await install_workspace_agent_package(archive, registry2)
    installed2 = await install_workspace_agent_package(
        archive, registry2, unpack_dir=tmp_path / "home"
    )
    assert installed2.id == spec.id


def test_resave_of_loaded_package_re_embeds_skills(tmp_path: Path) -> None:
    skill_dir = make_skill_dir(tmp_path)
    root = save_workspace_agent_package(make_spec(skill_dir), tmp_path / "pkg")
    loaded = load_workspace_agent_package(root)

    # Loaded skill sources are absolute; saving elsewhere embeds them again.
    second = save_workspace_agent_package(loaded, tmp_path / "pkg2")
    assert (second / "skills" / "reporting" / "templates" / "weekly.md").is_file()
    reloaded = load_workspace_agent_package(second)
    assert reloaded.id == loaded.id
