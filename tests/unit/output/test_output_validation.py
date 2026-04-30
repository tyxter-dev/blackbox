from __future__ import annotations

import builtins
import json
from dataclasses import dataclass
from typing import Any

from agent_runtime.runtime.output import _validate_output


@dataclass
class DataclassDecision:
    action: str
    confidence: float


def test_dataclass_output_validation_does_not_import_pydantic(
    monkeypatch: Any,
) -> None:
    original_import = builtins.__import__

    def fail_on_pydantic_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "pydantic" or name.startswith("pydantic."):
            raise AssertionError("dataclass validation should not import pydantic")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_on_pydantic_import)

    result = _validate_output(
        json.dumps({"action": "ship", "confidence": 0.99}),
        DataclassDecision,
    )

    assert result == DataclassDecision(action="ship", confidence=0.99)
