"""On-disk package format for workspace agents.

Turns :class:`~blackbox.workspace_agents.spec.WorkspaceAgentSpec` from a
dataclass into a distribution format: a directory (or zip archive) that can
be checked into a repo, diffed, published, and installed into a
:class:`~blackbox.workspace_agents.registry.WorkspaceAgentRegistry`.

Layout::

    my-agent/
      agent.json          # manifest: format marker + serialized spec
      instructions.md     # the agent's instructions, editable and diffable
      skills/<name>/...   # embedded skill bundles (arbitrary files)

Semantics:

- ``instructions`` live in ``instructions.md``, not the manifest, so prompt
  edits show up as readable diffs. On load the file wins over any manifest
  value.
- Skill bundles whose ``SkillBundleRef.source`` points at a local file or
  directory are **embedded**: copied under ``skills/<name>/`` with the ref
  rewritten to the package-relative path. Non-local sources (URLs, registry
  coordinates) are left verbatim as references. On load, embedded sources
  are rewritten to absolute paths under the package root so consumers can
  read them directly.
- The manifest carries ``format`` and ``format_version`` markers; loading a
  newer major format than this module understands fails rather than
  guessing.
- Unpacking guards against zip-slip: archive members may not be absolute or
  escape the destination.

Round-trip fidelity matches the existing dict serialization
(``workspace_agent_to_dict`` / ``workspace_agent_from_dict``): typed
``hosted_tools`` entries serialize to plain dicts and stay dicts on load.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath

from blackbox.workspace_agents.registry import WorkspaceAgentRegistry
from blackbox.workspace_agents.serialization import (
    workspace_agent_from_dict,
    workspace_agent_to_dict,
)
from blackbox.workspace_agents.spec import SkillBundleRef, WorkspaceAgentSpec

PACKAGE_FORMAT = "blackbox/workspace-agent"
PACKAGE_FORMAT_VERSION = 1

MANIFEST_FILENAME = "agent.json"
INSTRUCTIONS_FILENAME = "instructions.md"
SKILLS_DIRNAME = "skills"


class WorkspaceAgentPackageError(ValueError):
    """Raised for malformed, incompatible, or unsafe agent packages."""


# --- save ----------------------------------------------------------------------


def save_workspace_agent_package(
    spec: WorkspaceAgentSpec,
    root: Path | str,
    *,
    overwrite: bool = False,
    skills_base: Path | str | None = None,
) -> Path:
    """Write ``spec`` as a package directory at ``root`` and return ``root``.

    Local skill sources are embedded (copied under ``skills/<name>/``).
    Relative skill sources resolve against ``skills_base`` (default: the
    current working directory). Refuses to overwrite an existing package
    unless ``overwrite=True``.
    """

    package_root = Path(root)
    manifest_path = package_root / MANIFEST_FILENAME
    if manifest_path.exists() and not overwrite:
        raise WorkspaceAgentPackageError(
            f"Package already exists at {package_root}; pass overwrite=True to replace it."
        )
    package_root.mkdir(parents=True, exist_ok=True)

    base = Path(skills_base) if skills_base is not None else Path.cwd()
    embedded_skills = [_embed_skill(ref, package_root, base) for ref in spec.skills]
    portable = replace(spec, skills=embedded_skills)

    data = workspace_agent_to_dict(portable)
    instructions = data.pop("instructions", "") or ""
    (package_root / INSTRUCTIONS_FILENAME).write_text(instructions, encoding="utf-8")

    manifest = {
        "format": PACKAGE_FORMAT,
        "format_version": PACKAGE_FORMAT_VERSION,
        "spec": data,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return package_root


def _embed_skill(ref: SkillBundleRef, package_root: Path, base: Path) -> SkillBundleRef:
    if not ref.source:
        return ref
    relative_marker = f"{SKILLS_DIRNAME}/"
    if ref.source.replace("\\", "/").startswith(relative_marker):
        # Already package-relative (e.g. re-saving a loaded package whose
        # source was never rewritten); copy nothing, keep the reference.
        return ref
    source_path = Path(ref.source)
    if not source_path.is_absolute():
        source_path = base / source_path
    if not source_path.exists():
        # Non-local reference (URL, registry coordinate, missing path):
        # leave it verbatim for downstream resolution.
        return ref
    target = package_root / SKILLS_DIRNAME / ref.name
    if source_path.is_dir():
        shutil.copytree(source_path, target, dirs_exist_ok=True)
    else:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target / source_path.name)
    return replace(ref, source=f"{SKILLS_DIRNAME}/{ref.name}")


# --- load ----------------------------------------------------------------------


def load_workspace_agent_package(root: Path | str) -> WorkspaceAgentSpec:
    """Load a package directory back into a :class:`WorkspaceAgentSpec`.

    Embedded skill sources are rewritten to absolute paths under ``root``.
    """

    package_root = Path(root)
    manifest_path = package_root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise WorkspaceAgentPackageError(
            f"No {MANIFEST_FILENAME} found at {package_root}; not an agent package."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkspaceAgentPackageError(f"Malformed manifest at {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise WorkspaceAgentPackageError(f"Manifest at {manifest_path} must be a JSON object.")

    declared_format = manifest.get("format")
    if declared_format != PACKAGE_FORMAT:
        raise WorkspaceAgentPackageError(
            f"Unsupported package format {declared_format!r}; expected {PACKAGE_FORMAT!r}."
        )
    declared_version = manifest.get("format_version")
    if not isinstance(declared_version, int) or declared_version > PACKAGE_FORMAT_VERSION:
        raise WorkspaceAgentPackageError(
            f"Package format version {declared_version!r} is newer than the supported "
            f"version {PACKAGE_FORMAT_VERSION}; upgrade blackbox to load it."
        )
    spec_data = manifest.get("spec")
    if not isinstance(spec_data, dict):
        raise WorkspaceAgentPackageError(f"Manifest at {manifest_path} carries no spec object.")

    instructions_path = package_root / INSTRUCTIONS_FILENAME
    if instructions_path.is_file():
        spec_data = {
            **spec_data,
            "instructions": instructions_path.read_text(encoding="utf-8"),
        }

    spec = workspace_agent_from_dict(spec_data)
    resolved_skills = [_resolve_skill(ref, package_root) for ref in spec.skills]
    return replace(spec, skills=resolved_skills)


def _resolve_skill(ref: SkillBundleRef, package_root: Path) -> SkillBundleRef:
    if not ref.source:
        return ref
    normalized = ref.source.replace("\\", "/")
    if not normalized.startswith(f"{SKILLS_DIRNAME}/"):
        return ref
    bundled = package_root / PurePosixPath(normalized)
    if not bundled.exists():
        raise WorkspaceAgentPackageError(
            f"Skill {ref.name!r} declares embedded source {ref.source!r} "
            f"but {bundled} does not exist."
        )
    return replace(ref, source=str(bundled.resolve()))


# --- pack / unpack ---------------------------------------------------------------


def pack_workspace_agent_package(root: Path | str, archive: Path | str) -> Path:
    """Zip a saved package directory into ``archive`` and return its path."""

    package_root = Path(root)
    if not (package_root / MANIFEST_FILENAME).is_file():
        raise WorkspaceAgentPackageError(
            f"No {MANIFEST_FILENAME} found at {package_root}; save the package first."
        )
    archive_path = Path(archive)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(package_root.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(package_root).as_posix())
    return archive_path


def unpack_workspace_agent_package(archive: Path | str, dest: Path | str) -> Path:
    """Extract a package archive into ``dest`` (zip-slip guarded) and return ``dest``."""

    dest_root = Path(dest)
    dest_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(Path(archive)) as zf:
        for member in zf.namelist():
            _check_member_path(member)
        zf.extractall(dest_root)
    return dest_root


def _check_member_path(member: str) -> None:
    normalized = member.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or (len(normalized) > 1 and normalized[1] == ":"):
        raise WorkspaceAgentPackageError(f"Archive member has an absolute path: {member!r}")
    if ".." in pure.parts:
        raise WorkspaceAgentPackageError(f"Archive member escapes the destination: {member!r}")


# --- install ---------------------------------------------------------------------


def _staged_package_root(source: Path | str, unpack_dir: Path | str | None) -> Path:
    source_path = Path(source)
    if source_path.is_dir():
        return source_path
    if zipfile.is_zipfile(source_path):
        if unpack_dir is None:
            raise WorkspaceAgentPackageError(
                "Installing from an archive requires unpack_dir: embedded skills "
                "must live somewhere durable after extraction."
            )
        return unpack_workspace_agent_package(source_path, unpack_dir)
    raise WorkspaceAgentPackageError(
        f"{source_path} is neither a package directory nor a zip archive."
    )


async def install_workspace_agent_package(
    source: Path | str,
    registry: WorkspaceAgentRegistry,
    *,
    unpack_dir: Path | str | None = None,
) -> WorkspaceAgentSpec:
    """Load a package directory or archive and save its spec into ``registry``.

    Archives require ``unpack_dir`` — the extracted directory is the durable
    home of embedded skills, so the caller chooses where it lives.
    """

    spec = load_workspace_agent_package(_staged_package_root(source, unpack_dir))
    return await registry.save(spec)
