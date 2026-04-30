"""Optional wrapper for raw provider payloads carried on events.

Adapters may stash raw payloads on ``AgentEvent.raw`` directly, but if the
payload contains sensitive data (PII, tokens, customer secrets), wrap it in a
:class:`RawEnvelope` so downstream sinks can honor the redaction policy
without having to inspect every adapter's shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Sensitivity = Literal["public", "internal", "sensitive", "secret"]
RedactionStatus = Literal["raw", "redacted", "hash_only"]


@dataclass(slots=True, frozen=True)
class RawEnvelope:
    """Raw provider payload tagged with sensitivity and redaction policy.

    Default values (``sensitivity="internal"``, ``redaction_status="raw"``,
    ``storage_allowed=True``) match the behavior of unwrapped ``event.raw``
    values today, so wrapping is opt-in.
    """

    provider: str
    payload: Any
    schema_name: str | None = None
    schema_version: str | None = None
    sensitivity: Sensitivity = "internal"
    redaction_status: RedactionStatus = "raw"
    storage_allowed: bool = True
    hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def redacted(self, marker: str = "<redacted>") -> RawEnvelope:
        """Return a copy with payload replaced by the redaction marker."""
        return RawEnvelope(
            provider=self.provider,
            payload=marker,
            schema_name=self.schema_name,
            schema_version=self.schema_version,
            sensitivity=self.sensitivity,
            redaction_status="redacted",
            storage_allowed=self.storage_allowed,
            hash=self.hash,
            metadata=dict(self.metadata),
        )
