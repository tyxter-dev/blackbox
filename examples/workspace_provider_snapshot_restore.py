"""Minimal WorkspaceProvider snapshot and restore flow.

This creates a local workspace, snapshots it, and restores it into
examples/workspaces/restored-demo.

Run:

    python examples/workspace_provider_snapshot_restore.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import shutil
from pathlib import Path

from _bootstrap import bootstrap

bootstrap()

from agent_runtime.workspaces import LocalWorkspaceProvider, WorkspaceSpec


async def main() -> None:
    examples_dir = Path(__file__).parent
    source_root = examples_dir / "workspaces" / "snapshot-source"
    restored_root = examples_dir / "workspaces" / "restored-demo"

    source_root.mkdir(parents=True, exist_ok=True)
    if restored_root.exists():
        shutil.rmtree(restored_root)

    provider = LocalWorkspaceProvider()
    ws = await provider.open(WorkspaceSpec.local(source_root))
    await provider.write_file(ws, "state/message.txt", "snapshot restored successfully\n")

    snapshot = await provider.snapshot(ws, name="workspace-provider-demo")
    print(f"snapshot: {snapshot.name} {snapshot.uri}")

    restored = await provider.restore(
        snapshot.ref,
        spec=WorkspaceSpec.local(restored_root),
    )
    restored_text = await provider.read_file(restored, "state/message.txt")

    print(f"restored: {restored.id} root={restored.root}")
    print(restored_text.strip())

    artifacts = await provider.list_artifacts(type="workspace_snapshot")
    print("snapshot artifacts:")
    for artifact in artifacts.items:
        print(f"  {artifact.name} ({artifact.id})")


if __name__ == "__main__":
    asyncio.run(main())
