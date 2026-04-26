from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def pytest_configure(config: Any) -> None:
    if not _should_load_dotenv(config):
        return
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or name in os.environ:
            continue
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        os.environ[name] = value


def _should_load_dotenv(config: Any) -> bool:
    mark_expression = str(getattr(config.option, "markexpr", "") or "")
    if "integration_" in mark_expression or "journey" in mark_expression:
        return True
    args = [str(arg).replace("\\", "/") for arg in getattr(config, "args", [])]
    return any("tests/integration" in arg or "tests/journey" in arg for arg in args)
