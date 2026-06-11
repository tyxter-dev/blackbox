from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_LIVE_PATH_FRAGMENTS = ("tests/integration", "tests/journey")
_LIVE_MARK_FRAGMENTS = ("integration_", "journey")


def pytest_configure(config: Any) -> None:
    if not _live_tests_selected_explicitly(config):
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


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Keep the default suite offline.

    Live integration and journey tests run only when explicitly selected by
    marker or path, even if provider API keys are present in the environment.
    """
    if _live_tests_selected_explicitly(config):
        return
    deselected = [item for item in items if _is_live_item(item)]
    if not deselected:
        return
    config.hook.pytest_deselected(items=deselected)
    selected = {id(item) for item in deselected}
    items[:] = [item for item in items if id(item) not in selected]


def _live_tests_selected_explicitly(config: Any) -> bool:
    mark_expression = str(getattr(config.option, "markexpr", "") or "")
    if any(fragment in mark_expression for fragment in _LIVE_MARK_FRAGMENTS):
        return True
    args = [str(arg).replace("\\", "/") for arg in getattr(config, "args", [])]
    return any(
        fragment in arg for arg in args for fragment in _LIVE_PATH_FRAGMENTS
    )


def _is_live_item(item: Any) -> bool:
    path = str(getattr(item, "fspath", "") or "").replace("\\", "/")
    return any(fragment in path for fragment in _LIVE_PATH_FRAGMENTS)
