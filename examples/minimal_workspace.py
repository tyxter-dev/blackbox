"""Smallest workspace integration.

Opens a local workspace through ``runtime.workspaces``, writes a file, runs a
command, and closes the workspace.

Run:

    python examples/minimal_workspace.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import sys
from pathlib import Path

from _bootstrap import bootstrap

bootstrap()

from agent_runtime import AgentRuntime
from agent_runtime.workspaces import CommandSpec, WorkspaceSpec


async def main() -> None:
    runtime = AgentRuntime()
    workspace_root = Path(__file__).parent / "workspaces" / "minimal-demo"
    workspace_root.mkdir(parents=True, exist_ok=True)

    workspace = await runtime.workspaces.open(WorkspaceSpec.local(workspace_root))
    await runtime.workspaces.write_file(workspace, "notes.txt", "hello workspace\n")

    command = CommandSpec(
        command=[
            sys.executable,
            "-c",
            "from pathlib import Path; print(Path('notes.txt').read_text().strip())",
        ],
        shell=False,
    )
    result = await runtime.workspaces.run_command(workspace, command)

    print(result.stdout.strip())
    await runtime.workspaces.close(workspace)


if __name__ == "__main__":
    asyncio.run(main())
