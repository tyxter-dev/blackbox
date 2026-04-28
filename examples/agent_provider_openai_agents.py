"""Run an OpenAI Agents SDK-backed AgentProvider session.

Requires:
    - OPENAI_API_KEY
    - the optional openai-agents package used by OpenAICloudAgentProvider

Run:

    python examples/agent_provider_openai_agents.py
"""
from __future__ import annotations

# ruff: noqa: E402
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap
from dotenv import load_dotenv

bootstrap()

from agent_runtime import AgentRuntime, AgentSpec, Artifact, EventTypes
from agent_runtime.providers.agent_adapters.openai_cloud import OpenAICloudAgentProvider

load_dotenv()


async def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY before running this example.")

    runtime = AgentRuntime()
    runtime.registry.register_agent(OpenAICloudAgentProvider())

    agent = await runtime.agents.create_agent(
        provider="openai-agents",
        spec=AgentSpec(
            name="release-notes-writer",
            instructions=(
                "You turn engineering change summaries into concise release notes."
            ),
            model=os.getenv("OPENAI_AGENT_MODEL", "gpt-5.5"),
        ),
    )
    session = await runtime.agents.create_session(
        provider="openai-agents",
        agent=agent,
        task=(
            "Draft release notes for: provider-native hosted tools, "
            "model-specific capability validation, and provider state preservation."
        ),
    )

    async for event in runtime.agents.stream(session):
        if event.type == EventTypes.MODEL_TEXT_DELTA:
            print(event.data.get("delta", ""), end="")
        elif event.type == EventTypes.CLOUD_AGENT_STATUS_CHANGED:
            print(f"\n{event.type}: {event.data.get('status', 'updated')}")
        elif event.type == EventTypes.SESSION_COMPLETED:
            print(f"\n{event.type}: {event.data.get('message', '')}")
        elif event.type in {
            EventTypes.SESSION_STARTED,
            EventTypes.APPROVAL_REQUESTED,
            EventTypes.SESSION_FAILED,
        }:
            print(f"\n{event.type}: {event.data}")

    artifacts = await runtime.agents.list_artifacts(session, limit=20)
    for artifact in artifacts.items:
        print(f"artifact: {artifact.type} {artifact.name} ({artifact.id})")
        saved = _save_artifact(artifact, Path(__file__).parent / "artifacts" / "openai")
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
