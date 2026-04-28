from __future__ import annotations

import os
import sys
from pathlib import Path


def bootstrap(*, load_env: bool = False) -> None:
    """Prefer the local checkout when examples are run as scripts."""

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    if src_dir.exists():
        sys.path.insert(0, str(src_dir))
    if load_env:
        load_examples_env()


def load_examples_env(path: Path | None = None) -> None:
    """Load simple KEY=value pairs from ``examples/.env`` without extra deps."""

    env_path = path or Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if not name or name in os.environ:
            continue
        os.environ[name] = value.strip().strip('"').strip("'")


def example_model(default: str = "gpt-4o-mini") -> str:
    """Resolve the model env vars used by runnable examples."""

    return (
        os.getenv("OPENAI_EXAMPLE_MODEL")
        or os.getenv("OPENAI_AGENT_MODEL")
        or os.getenv("OPENAI_TEST_MODEL")
        or default
    )
