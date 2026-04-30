"""Minimal local WorkspaceProvider file, patch, command, and artifact flow.

This writes into examples/workspaces/local-demo so the environment side effects
are easy to inspect after the script exits.

Run:

    python examples/workspace_provider_local_files.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import sys
from pathlib import Path

from _bootstrap import bootstrap

bootstrap()

from blackbox.core.events import EventTypes
from blackbox.workspaces import (
    CommandSpec,
    FileChange,
    LocalWorkspaceProvider,
    Patch,
    WorkspaceSpec,
)


async def main() -> None:
    """Run the local workspace provider demo for file, patch, command, and artifact flows."""
    workspace_root = Path(__file__).parent / "workspaces" / "local-demo"
    workspace_root.mkdir(parents=True, exist_ok=True)

    provider = LocalWorkspaceProvider()
    ws = await provider.open(WorkspaceSpec.local(workspace_root))
    print(f"opened: {ws.id} root={ws.root}")

    change = await provider.write_file(
        ws,
        "notes/summary.md",
        "# WorkspaceProvider demo\n\nThis file was written through the provider.\n",
    )
    print(f"write: {change.type} {change.path}")

    content = await provider.read_file(ws, "notes/summary.md")
    print(f"read: {content.splitlines()[0]}")

    patch = await provider.apply_patch(
        ws,
        Patch(
            summary="add checklist",
            diff="create notes/checklist.md",
            changes=[
                FileChange(
                    path="notes/checklist.md",
                    type="create",
                    content="- open workspace\n- write file\n- run command\n",
                )
            ],
        ),
    )
    print(f"patch: {patch.id} {patch.summary}")

    result = await provider.run_command(
        ws,
        CommandSpec(
            command=[
                sys.executable,
                "-c",
                "from pathlib import Path; print(Path('notes/checklist.md').read_text())",
            ],
            shell=False,
            metadata={"artifact": True},
        ),
    )
    print(f"command exit={result.exit_code}")
    print(result.stdout.strip())

    files = await provider.list_files(ws, recursive=True)
    print("files:")
    for file in files:
        print(f"  {file}")

    artifacts = await provider.list_artifacts(ws)
    print("artifacts:")
    for artifact in artifacts.items:
        print(f"  {artifact.type}: {artifact.name} ({artifact.id})")

    print("events:")
    for event in provider.drain_events():
        if event.type in {
            EventTypes.WORKSPACE_FILE_CHANGED,
            EventTypes.WORKSPACE_PATCH_CREATED,
            EventTypes.WORKSPACE_COMMAND_COMPLETED,
            EventTypes.ARTIFACT_CREATED,
        }:
            print(f"  {event.type}: {event.data}")


if __name__ == "__main__":
    asyncio.run(main())
