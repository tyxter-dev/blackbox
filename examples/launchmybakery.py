"""Launch My Bakery rebuilt with Blackbox MCP toolsets.

This translates Google's ADK example into the library's high-level runtime:
the model receives two Google-hosted remote MCP servers, one for Maps and one
for BigQuery. The BigQuery server is scoped to the synthetic ``mcp_bakery``
dataset that the upstream setup scripts load.

Prerequisites:

    pip install -e .[openai,google]
    gcloud auth application-default login

Set these environment variables:

    OPENAI_API_KEY=...
    MAPS_API_KEY=...
    GOOGLE_CLOUD_PROJECT=...

Run:

    python examples/launchmybakery.py
    python examples/launchmybakery.py "Find the best morning foot-traffic zip code."

The default model is OpenAI Responses because this runtime can pass remote MCP
servers through provider-native MCP there. Set ``AGENT_RUNTIME_BAKERY_PROVIDER``
to any registered provider/model reference and ``AGENT_RUNTIME_BAKERY_MCP_MODE``
to ``local`` or ``provider_native`` if you want to test a different route.
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if SRC_DIR.exists():
    sys.path.insert(0, str(SRC_DIR))

from blackbox import (  # noqa: E402
    AgentRuntime,
    MCPToolset,
    create_runtime_with_default_providers,
)
from blackbox.integrations import (  # noqa: E402
    google_bigquery_mcp_toolset,
    google_maps_mcp_toolset,
)
from blackbox.integrations.google import MCPRouteMode  # noqa: E402

DEFAULT_PROVIDER = "openai:gpt-5.4"
DEFAULT_QUESTION = (
    "I am looking to open my fourth bakery location in Los Angeles. "
    "Find the zip code with the highest morning foot traffic score, check "
    "nearby bakery competition, recommend a premium sourdough price point, "
    "and estimate December 2025 sourdough revenue using the best comparable "
    "store trend."
)


def bakery_instructions(project_id: str) -> str:
    return f"""
Help the user answer questions by strategically combining insights from two sources:

1. BigQuery toolset: Access demographic, foot traffic, product pricing, and
   historical sales data in the mcp_bakery dataset. Do not use any other
   dataset. Run all query jobs from project id: {project_id}.
2. Maps toolset: Use this for real-world location analysis, finding competition
   or nearby places, and calculating travel routes. Include a hyperlink to an
   interactive map in your response where appropriate.

Ground every business recommendation in the available data. If a requested
metric is missing, say exactly what is missing and use the closest available
evidence instead of inventing values.
""".strip()


def bakery_toolsets(*, mode: MCPRouteMode) -> list[MCPToolset]:
    """Compose the app-specific Google MCP tools for the bakery demo."""
    return [
        google_maps_mcp_toolset(mode=mode),
        google_bigquery_mcp_toolset(mode=mode, dataset="mcp_bakery"),
    ]


async def run_bakery_agent(question: str, *, provider: str | None = None) -> str:
    provider_ref = provider or os.getenv("AGENT_RUNTIME_BAKERY_PROVIDER", DEFAULT_PROVIDER)
    runtime: AgentRuntime = create_runtime_with_default_providers()
    toolsets = bakery_toolsets(mode=_bakery_mcp_mode())
    project_id = str(toolsets[1].server.headers["x-goog-user-project"])

    result = await runtime.run(
        provider=provider_ref,
        input=question,
        instructions=bakery_instructions(project_id),
        toolsets=toolsets,
        max_iterations=int(os.getenv("AGENT_RUNTIME_BAKERY_MAX_ITERATIONS", "8")),
        max_output_tokens=int(os.getenv("AGENT_RUNTIME_BAKERY_MAX_OUTPUT_TOKENS", "4096")),
    )
    return result.text


async def main(argv: Sequence[str] | None = None) -> None:
    _load_dotenv(REPO_ROOT / ".env")
    args = list(sys.argv[1:] if argv is None else argv)
    question = " ".join(args) if args else os.getenv("AGENT_RUNTIME_BAKERY_QUESTION", DEFAULT_QUESTION)

    answer = await run_bakery_agent(question)
    print(answer)


def _bakery_mcp_mode() -> MCPRouteMode:
    mode = os.getenv("AGENT_RUNTIME_BAKERY_MCP_MODE", "provider_native")
    if mode not in {"auto", "local", "provider_native"}:
        raise RuntimeError(
            "AGENT_RUNTIME_BAKERY_MCP_MODE must be one of: auto, local, provider_native."
        )
    return mode


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


if __name__ == "__main__":
    asyncio.run(main())
