"""Run the diet-coach flagship example end to end as a subprocess.

The example exercises the whole Horizon 2 surface offline (spec validation,
on-disk package, archive install into the SQLite registry, scheduled run,
conversational preference change, second scheduled run). This test pins
that the published story keeps working.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "diet_coach.py"


def test_diet_coach_example_runs_offline() -> None:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, str(EXAMPLE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
        cwd=EXAMPLE.parent,
        env=env,
    )
    assert result.returncode == 0, result.stderr

    out = result.stdout
    # Validation surfaced the expected catalog warning and nothing else.
    assert "unknown_model" in out
    # The package was written and installed from the archive.
    assert "skills/nutrition/SKILL.md" in out
    assert "installed into SQLite registry: diet-coach" in out
    # Monday's 9 AM run posted the chicken plan; the conversation switched to
    # beef; Tuesday's run reflects the stored preference.
    assert "lunch: chicken with rice" in out
    assert "swapped chicken for beef" in out
    assert "lunch: beef with rice" in out
    assert out.count("morning-update") >= 2
