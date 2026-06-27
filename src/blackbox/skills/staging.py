from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from blackbox.core.errors import ConfigurationError
from blackbox.skills.specs import SkillSpec


class SkillStager(Protocol):
    async def prepare(
        self,
        skills: Sequence[SkillSpec],
        *,
        workspace: Any,
        provider: Any,
    ) -> None: ...


class ClaudeCodeSkillStager:
    """Stage skill bundles into `.claude/skills/` for Claude Code sessions."""

    async def prepare(
        self,
        skills: Sequence[SkillSpec],
        *,
        workspace: Any,
        provider: Any,
    ) -> None:
        for skill in skills:
            source = _materialized_skill_source(skill)
            await _stage_directory(
                source,
                destination=f".claude/skills/{skill.name}",
                workspace=workspace,
                provider=provider,
            )


def ensure_project_setting_source(extra: dict[str, Any]) -> dict[str, Any]:
    current = extra.get("setting_sources")
    if current is None:
        extra["setting_sources"] = ["project"]
        return extra
    if isinstance(current, str):
        sources = [current]
    elif isinstance(current, list):
        sources = [str(value) for value in current]
    else:
        raise ConfigurationError("setting_sources must be a string or list of strings.")
    if "project" not in sources:
        sources.append("project")
    extra["setting_sources"] = sources
    return extra


def _materialized_skill_source(skill: SkillSpec) -> Path:
    if skill.source is not None:
        source = Path(skill.source).expanduser()
        if source.is_dir():
            return source
        if source.exists():
            raise ConfigurationError(f"Skill source is not a directory: {source}.")
    tempdir = Path(tempfile.mkdtemp(prefix=f"blackbox_skill_{skill.name}_"))
    (tempdir / "SKILL.md").write_text(skill.to_markdown(), encoding="utf-8")
    return tempdir


async def _stage_directory(
    source: Path,
    *,
    destination: str,
    workspace: Any,
    provider: Any,
) -> None:
    for relative, content in await asyncio.to_thread(_read_text_files, source):
        target = f"{destination}/{relative}"
        await provider.write_file(workspace, target, content)
    if source.name.startswith("blackbox_skill_") and source.parent == Path(tempfile.gettempdir()):
        await asyncio.to_thread(shutil.rmtree, source, ignore_errors=True)


def _read_text_files(source: Path) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(source).as_posix()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigurationError(
                f"Skill supporting file is not UTF-8 text and cannot be staged yet: {path}."
            ) from exc
        files.append((relative, content))
    return files
