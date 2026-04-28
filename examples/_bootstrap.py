from __future__ import annotations

import sys
from pathlib import Path


def bootstrap() -> None:
    """Prefer the local checkout when examples are run as scripts."""

    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    if src_dir.exists():
        sys.path.insert(0, str(src_dir))
