"""Audit a fleet's configured models against the provider model catalog.

Motivated by a real finding in ``docs/USE_CASE_VALIDATION.md``: 170 of 610
production agents were still configured with a model already past its
announced deprecation date. The bundled ``ProviderModelCatalog`` carries
lifecycle, replacement, and deprecation metadata, so a platform can audit
its agent fleet offline — no provider API calls.

Run::

    python examples/model_lifecycle_audit.py
"""
from __future__ import annotations

# ruff: noqa: E402
from collections import Counter
from datetime import UTC, datetime

from _bootstrap import bootstrap

bootstrap()

from blackbox import AgentRuntime

# A simulated agent fleet: (agent count, provider, configured model).
# Shaped like the production distribution in docs/USE_CASE_VALIDATION.md.
FLEET = [
    (354, "openai", "gpt-5.4-mini"),
    (170, "xai", "grok-4-1-fast-non-reasoning"),
    (67, "openai", "gpt-5-mini"),
    (5, "openai", "gpt-4.1"),
    (4, "xai", "grok-4"),
    (2, "openai", "gpt-4.1-mini-2025-04-14"),
    (1, "openai", "my-custom-finetune"),
]


def main() -> None:
    runtime = AgentRuntime()
    catalog = runtime.provider_model_catalog
    now = datetime.now(UTC)

    findings: Counter[str] = Counter()
    print(f"{'agents':>6}  {'provider':9} {'model':35} verdict")
    print("-" * 92)
    for count, provider, model in FLEET:
        entry = catalog.get(provider=provider, model=model)
        if entry is None:
            verdict = "NOT IN CATALOG - verify against provider docs or register it"
            findings["unknown_model"] += count
        elif entry.lifecycle in {"deprecated", "retired", "deprecating"}:
            deadline = entry.metadata.get("deprecates_at")
            overdue = False
            if isinstance(deadline, str):
                try:
                    overdue = datetime.fromisoformat(deadline) <= now
                except ValueError:
                    pass
            state = "PAST DEADLINE" if overdue else entry.lifecycle.upper()
            verdict = f"{state} -> migrate to {entry.replacement_model or '(no replacement listed)'}"
            if deadline:
                verdict += f" (deadline {deadline[:10]})"
            findings["past_deadline" if overdue else "deprecating"] += count
        elif entry.lifecycle == "unknown":
            verdict = "in catalog, lifecycle unknown - refresh the bundled catalog"
            findings["stale_catalog"] += count
        else:
            verdict = f"ok ({entry.lifecycle})"
            findings["ok"] += count
        print(f"{count:>6}  {provider:9} {model:35} {verdict}")

    total = sum(count for count, _, _ in FLEET)
    print("-" * 92)
    print(f"fleet: {total} agents | ok={findings['ok']} "
          f"past_deadline={findings['past_deadline']} "
          f"deprecating={findings['deprecating']} "
          f"unknown_model={findings['unknown_model']} "
          f"stale_catalog={findings['stale_catalog']}")

    if findings["past_deadline"]:
        print(f"\naction required: {findings['past_deadline']} agents run a model past its "
              "deprecation deadline and may already be failing or silently remapped.")


if __name__ == "__main__":
    main()
