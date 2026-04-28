"""Run a Claude Code Agent SDK-backed AgentProvider session.

Requires:
    - ANTHROPIC_API_KEY
    - the optional claude-agent-sdk package used by ClaudeCodeAgentProvider

Run:

    python examples/agent_provider_claude_code.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

bootstrap()

from agent_runtime import AgentRuntime, AgentSessionResult, AgentSpec, Artifact, EventTypes
from agent_runtime.providers.agent_adapters.claude_code import ClaudeCodeAgentProvider
from agent_runtime.workspaces import WorkspaceSpec


@dataclass(slots=True)
class CodeReviewSummary:
    summary: str
    risks: list[str]
    tests: list[str]


async def main() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running this example.")

    runtime = AgentRuntime()
    runtime.registry.register_agent(ClaudeCodeAgentProvider())

    agent = await runtime.agents.create_agent(
        provider="claude-code",
        spec=AgentSpec(
            name="repo-reviewer",
            instructions=(
                "Inspect the repository and return JSON matching the requested schema."
            ),
            model=os.getenv("CLAUDE_CODE_MODEL", "claude-sonnet"),
            tools=["Read", "Grep", "Glob"],
            permissions={"permission_mode": "default"},
        ),
    )
    result: AgentSessionResult[CodeReviewSummary] = await runtime.agents.run(
        provider="claude-code",
        agent=agent,
        task=(
            "Review this repository's AgentProvider surface. Return JSON with "
            "summary, risks, and tests."
        ),
        workspace=WorkspaceSpec.local(Path.cwd()),
        output_type=CodeReviewSummary,
        artifact_type="file_change",
    )

    print(f"status={result.status} session={result.session_ref.id}")
    print(result.output.summary)
    for risk in result.output.risks:
        print(f"risk: {risk}")
    for test in result.output.tests:
        print(f"test: {test}")

    for event in result.events:
        if event.type in {
            EventTypes.WORKSPACE_FILE_CHANGED,
            EventTypes.WORKSPACE_TEST_COMPLETED,
            EventTypes.APPROVAL_REQUESTED,
        }:
            print(f"{event.type}: {event.data}")

    for artifact in result.artifacts:
        print(f"artifact: {artifact.type} {artifact.name} ({artifact.id})")
        saved = _save_artifact(artifact, Path(__file__).parent / "artifacts" / "claude-code")
        if saved is not None:
            print(f"saved: {saved}")


def _save_artifact(artifact: Artifact, output_dir: Path) -> Path | None:
    if artifact.data is None and artifact.uri is None:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(artifact.name or artifact.id)
    path = output_dir / safe_name
    path.write_text(_artifact_text(artifact.data, fallback_uri=artifact.uri), encoding="utf-8")
    return path


def _artifact_text(data: Any, *, fallback_uri: str | None) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    if data is not None:
        return json.dumps(data, indent=2, sort_keys=True, default=str)
    return fallback_uri or ""


def _safe_filename(name: str) -> str:
    return "".join(char if char.isalnum() or char in {".", "-", "_"} else "_" for char in name)


if __name__ == "__main__":
    asyncio.run(main())
