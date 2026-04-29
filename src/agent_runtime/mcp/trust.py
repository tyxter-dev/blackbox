from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol


class MCPTrustLevel(StrEnum):
    BLOCKED = "blocked"
    UNTRUSTED = "untrusted"
    REVIEWED = "reviewed"
    TRUSTED = "trusted"
    FIRST_PARTY = "first_party"


class MCPRouteMode(StrEnum):
    DISABLED = "disabled"
    LOCAL_ONLY = "local_only"
    PROVIDER_NATIVE_ALLOWED = "provider_native_allowed"
    PROVIDER_NATIVE_REQUIRED = "provider_native_required"


class MCPApprovalMode(StrEnum):
    NEVER = "never"
    UNKNOWN_SERVER = "unknown_server"
    HIGH_RISK_TOOL = "high_risk_tool"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    ALWAYS = "always"
    POLICY = "policy"


class MCPCapabilityRisk(StrEnum):
    READS_WORKSPACE = "reads_workspace"
    WRITES_WORKSPACE = "writes_workspace"
    RUNS_COMMANDS = "runs_commands"
    NETWORK_EGRESS = "network_egress"
    SECRET_ACCESS = "secret_access"
    EXTERNAL_AUTH = "external_auth"
    STATEFUL_SESSION = "stateful_session"
    PROVIDER_NATIVE_EXECUTION = "provider_native_execution"
    SERVER_INITIATED_SAMPLING = "server_initiated_sampling"
    SERVER_ELICITATION = "server_elicitation"
    ARTIFACT_EXPORT = "artifact_export"


class MCPTaint(StrEnum):
    UNTAINTED = "untainted"
    EXTERNAL = "external"
    SECRET_ADJACENT = "secret_adjacent"
    WORKSPACE_PRIVATE = "workspace_private"
    USER_PRIVATE = "user_private"
    EXECUTION_OUTPUT = "execution_output"


@dataclass(slots=True, frozen=True)
class MCPToolTrustPolicy:
    ref: str
    trust_level: MCPTrustLevel = MCPTrustLevel.UNTRUSTED
    risks: frozenset[MCPCapabilityRisk] = frozenset()
    required_scopes: frozenset[str] = frozenset()
    approval_mode: MCPApprovalMode = MCPApprovalMode.POLICY
    max_output_bytes: int | None = None
    allow_provider_native: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "trust_level": self.trust_level.value,
            "risks": sorted(risk.value for risk in self.risks),
            "required_scopes": sorted(self.required_scopes),
            "approval_mode": self.approval_mode.value,
            "max_output_bytes": self.max_output_bytes,
            "allow_provider_native": self.allow_provider_native,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True, frozen=True)
class MCPServerTrustPolicy:
    server: str
    trust_level: MCPTrustLevel = MCPTrustLevel.UNTRUSTED
    route_mode: MCPRouteMode = MCPRouteMode.LOCAL_ONLY
    allowed_tools: frozenset[str] | None = None
    denied_tools: frozenset[str] = frozenset()
    allowed_scopes: frozenset[str] = frozenset()
    allowed_domains: frozenset[str] = frozenset()
    denied_domains: frozenset[str] = frozenset()
    approval_mode: MCPApprovalMode = MCPApprovalMode.UNKNOWN_SERVER
    require_sandbox: bool = True
    max_output_bytes: int | None = 64_000
    redact_secrets: bool = True
    metadata: dict[str, str] = field(default_factory=dict)

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "trust_level": self.trust_level.value,
            "route_mode": self.route_mode.value,
            "allowed_tools": sorted(self.allowed_tools) if self.allowed_tools is not None else None,
            "denied_tools": sorted(self.denied_tools),
            "allowed_scopes": sorted(self.allowed_scopes),
            "allowed_domains": sorted(self.allowed_domains),
            "denied_domains": sorted(self.denied_domains),
            "approval_mode": self.approval_mode.value,
            "require_sandbox": self.require_sandbox,
            "max_output_bytes": self.max_output_bytes,
            "redact_secrets": self.redact_secrets,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True, frozen=True)
class MCPServerRiskProfile:
    server: str
    transport: str
    source: str | None = None
    command: tuple[str, ...] = ()
    cwd: str | None = None
    url_origin: str | None = None
    protocol_version: str | None = None
    auth_mode: str | None = None
    requested_scopes: tuple[str, ...] = ()
    declared_tools: tuple[str, ...] = ()
    capability_risks: frozenset[MCPCapabilityRisk] = frozenset()
    package_name: str | None = None
    package_version: str | None = None
    package_digest: str | None = None
    trust_fingerprint: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class MCPTrustDecision:
    allowed: bool
    trust_level: MCPTrustLevel
    route_mode: MCPRouteMode
    approval_required: bool
    reasons: tuple[str, ...] = ()
    redactions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class MCPTrustEvaluator(Protocol):
    def evaluate_server(
        self,
        server_spec: object,
        policy: MCPServerTrustPolicy | None,
    ) -> MCPTrustDecision: ...

    def evaluate_tool(
        self,
        server_policy: MCPServerTrustPolicy,
        tool_policy: MCPToolTrustPolicy | None,
        tool_definition: object,
    ) -> MCPTrustDecision: ...


class DefaultMCPTrustEvaluator:
    """Conservative local trust evaluator for MCP server and tool exposure."""

    def evaluate_server(
        self,
        server_spec: object,
        policy: MCPServerTrustPolicy | None,
    ) -> MCPTrustDecision:
        effective = policy or effective_server_trust_policy(server_spec)
        reasons: list[str] = []
        if effective.trust_level == MCPTrustLevel.UNTRUSTED:
            reasons.append("server_untrusted")
        if effective.trust_level == MCPTrustLevel.BLOCKED:
            reasons.append("server_blocked")
        if effective.route_mode == MCPRouteMode.DISABLED:
            reasons.append("mcp_route_disabled")
        allowed = effective.trust_level != MCPTrustLevel.BLOCKED and (
            effective.route_mode != MCPRouteMode.DISABLED
        )
        return MCPTrustDecision(
            allowed=allowed,
            trust_level=effective.trust_level,
            route_mode=effective.route_mode,
            approval_required=_approval_required(effective.approval_mode, effective.trust_level),
            reasons=tuple(reasons),
            redactions=("secrets",) if effective.redact_secrets else (),
            metadata={
                "server": effective.server,
                "require_sandbox": effective.require_sandbox,
                "fingerprint": trust_fingerprint(server_spec),
            },
        )

    def evaluate_tool(
        self,
        server_policy: MCPServerTrustPolicy,
        tool_policy: MCPToolTrustPolicy | None,
        tool_definition: object,
    ) -> MCPTrustDecision:
        name = _tool_name(tool_definition)
        ref = _tool_ref(tool_definition, server_policy.server, name)
        reasons: list[str] = []
        risks = _tool_risks(tool_definition)
        if tool_policy is not None:
            risks = frozenset({*risks, *tool_policy.risks})
        trust_level = tool_policy.trust_level if tool_policy is not None else server_policy.trust_level

        allowed = True
        quarantined = False
        if trust_level == MCPTrustLevel.BLOCKED:
            allowed = False
            reasons.append("tool_blocked")
        if name in server_policy.denied_tools or ref in server_policy.denied_tools:
            allowed = False
            reasons.append("tool_denied")
        if server_policy.allowed_tools is not None and (
            name not in server_policy.allowed_tools and ref not in server_policy.allowed_tools
        ):
            allowed = False
            reasons.append("tool_not_allowlisted")
        if (
            allowed
            and server_policy.allowed_tools is None
            and tool_policy is None
            and server_policy.trust_level == MCPTrustLevel.UNTRUSTED
        ):
            allowed = False
            quarantined = True
            reasons.append("untrusted_tool_quarantined")
        if (
            allowed
            and server_policy.allowed_tools is None
            and tool_policy is None
            and _has_high_risk(risks)
            and server_policy.trust_level in {MCPTrustLevel.UNTRUSTED, MCPTrustLevel.REVIEWED}
        ):
            allowed = False
            quarantined = True
            reasons.append("high_risk_tool_quarantined")

        approval_mode = tool_policy.approval_mode if tool_policy is not None else server_policy.approval_mode
        approval_required = _approval_required(
            approval_mode,
            trust_level,
            high_risk=_has_high_risk(risks),
        )
        return MCPTrustDecision(
            allowed=allowed,
            trust_level=trust_level,
            route_mode=server_policy.route_mode,
            approval_required=approval_required,
            reasons=tuple(reasons),
            redactions=("secrets",) if server_policy.redact_secrets else (),
            metadata={
                "server": server_policy.server,
                "tool": name,
                "ref": ref,
                "risks": sorted(risk.value for risk in risks),
                "quarantined": quarantined,
                "required_scopes": sorted(tool_policy.required_scopes if tool_policy else ()),
            },
        )


def effective_server_trust_policy(server_spec: object) -> MCPServerTrustPolicy:
    """Return the trust policy that applies to a server spec.

    Existing ``allowed_tools`` / ``denied_tools`` fields remain authoritative as
    local runtime filters, so toolset-level overrides can narrow a configured
    trust policy without mutating it.
    """

    server = str(getattr(server_spec, "name", ""))
    configured = getattr(server_spec, "trust_policy", None)
    base = configured if isinstance(configured, MCPServerTrustPolicy) else MCPServerTrustPolicy(server=server)
    spec_allowed = getattr(server_spec, "allowed_tools", None)
    spec_denied = getattr(server_spec, "denied_tools", None)
    if isinstance(configured, MCPServerTrustPolicy):
        max_output_bytes = base.max_output_bytes
        redact = base.redact_secrets
    else:
        max_output_bytes = getattr(server_spec, "max_output_bytes", base.max_output_bytes)
        redact = bool(getattr(server_spec, "redact", base.redact_secrets))
    return replace(
        base,
        server=server,
        allowed_tools=frozenset(spec_allowed) if spec_allowed is not None else base.allowed_tools,
        denied_tools=frozenset({*base.denied_tools, *(spec_denied or ())}),
        max_output_bytes=max_output_bytes,
        redact_secrets=redact,
    )


def trust_fingerprint(server_spec: object) -> str:
    """Hash the security-relevant MCP server identity without secret values."""

    trust_policy = getattr(server_spec, "trust_policy", None)
    tool_policies = getattr(server_spec, "tool_policies", None)
    identity: dict[str, Any] = {
        "server": getattr(server_spec, "name", None),
        "transport": getattr(server_spec, "transport", None),
        "protocol_version": getattr(server_spec, "protocol_version", None),
        "command": getattr(server_spec, "command", None),
        "args": list(getattr(server_spec, "args", ()) or ()),
        "cwd": getattr(server_spec, "cwd", None),
        "url_origin": _url_origin(getattr(server_spec, "url", None)),
        "auth": {
            "authorization": getattr(server_spec, "authorization", None) is not None,
            "auth_provider_name": getattr(server_spec, "auth_provider_name", None),
            "headers": sorted(getattr(server_spec, "headers", {}) or {}),
            "env_keys": sorted(getattr(server_spec, "env", {}) or {}),
        },
        "allowed_tools": sorted(getattr(server_spec, "allowed_tools", None) or ()),
        "denied_tools": sorted(getattr(server_spec, "denied_tools", None) or ()),
        "require_approval": getattr(server_spec, "require_approval", None),
        "max_output_bytes": getattr(server_spec, "max_output_bytes", None),
        "redact": getattr(server_spec, "redact", None),
        "allow_provider_native": getattr(server_spec, "allow_provider_native", None),
        "require_sandbox": getattr(server_spec, "require_sandbox", None),
        "allowed_roots": sorted(getattr(server_spec, "allowed_roots", ()) or ()),
        "allowed_egress_domains": sorted(getattr(server_spec, "allowed_egress_domains", ()) or ()),
        "trust_policy": (
            trust_policy.to_redacted_dict()
            if isinstance(trust_policy, MCPServerTrustPolicy)
            else None
        ),
        "tool_policies": {
            key: value.to_redacted_dict()
            for key, value in sorted((tool_policies or {}).items())
            if isinstance(value, MCPToolTrustPolicy)
        },
    }
    return "sha256:" + _stable_hash(identity)


def sanitize_tool_description(description: str, *, max_chars: int = 2_000) -> str:
    """Remove control characters and cap model-visible MCP tool descriptions."""

    without_controls = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", description)
    normalized = re.sub(r"[ \t]+", " ", without_controls).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rstrip()


def default_taint_for_tool(
    definition: object,
    *,
    transport: str,
    trust_level: MCPTrustLevel,
) -> MCPTaint:
    risks = _tool_risks(definition)
    if MCPCapabilityRisk.SECRET_ACCESS in risks:
        return MCPTaint.SECRET_ADJACENT
    if MCPCapabilityRisk.READS_WORKSPACE in risks or MCPCapabilityRisk.WRITES_WORKSPACE in risks:
        return MCPTaint.WORKSPACE_PRIVATE
    if transport == "stdio" or MCPCapabilityRisk.RUNS_COMMANDS in risks:
        return MCPTaint.EXECUTION_OUTPUT
    if trust_level in {MCPTrustLevel.TRUSTED, MCPTrustLevel.FIRST_PARTY}:
        return MCPTaint.EXTERNAL
    return MCPTaint.USER_PRIVATE


def _approval_required(
    mode: MCPApprovalMode,
    trust_level: MCPTrustLevel,
    *,
    high_risk: bool = False,
) -> bool:
    if mode == MCPApprovalMode.NEVER:
        return False
    if mode == MCPApprovalMode.ALWAYS:
        return True
    if mode == MCPApprovalMode.UNKNOWN_SERVER:
        return trust_level in {MCPTrustLevel.UNTRUSTED, MCPTrustLevel.BLOCKED}
    if mode in {MCPApprovalMode.HIGH_RISK_TOOL, MCPApprovalMode.EXTERNAL_SIDE_EFFECT}:
        return high_risk
    return False


def _tool_name(tool_definition: object) -> str:
    if isinstance(tool_definition, dict):
        return str(tool_definition.get("name", ""))
    return str(getattr(tool_definition, "name", ""))


def _tool_ref(tool_definition: object, server: str, name: str) -> str:
    if isinstance(tool_definition, dict):
        value = tool_definition.get("ref")
        if isinstance(value, str):
            return value
    value = getattr(tool_definition, "ref", None)
    if isinstance(value, str):
        return value
    return f"mcp:{server}.{name}"


def _tool_risks(tool_definition: object) -> frozenset[MCPCapabilityRisk]:
    metadata = _tool_metadata(tool_definition)
    values: set[MCPCapabilityRisk] = set()
    raw_risks = metadata.get("risks")
    if isinstance(raw_risks, (list, tuple, set)):
        for item in raw_risks:
            try:
                values.add(MCPCapabilityRisk(str(item)))
            except ValueError:
                continue
    annotations = metadata.get("annotations")
    if isinstance(annotations, dict):
        if annotations.get("destructiveHint") is True:
            values.add(MCPCapabilityRisk.WRITES_WORKSPACE)
        if annotations.get("readOnlyHint") is True:
            values.add(MCPCapabilityRisk.NETWORK_EGRESS)
    if metadata.get("destructive") is True:
        values.add(MCPCapabilityRisk.WRITES_WORKSPACE)
    return frozenset(values)


def _tool_metadata(tool_definition: object) -> dict[str, Any]:
    if isinstance(tool_definition, dict):
        metadata = tool_definition.get("metadata")
        if isinstance(metadata, dict):
            return dict(metadata)
        return {}
    metadata = getattr(tool_definition, "metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _has_high_risk(risks: frozenset[MCPCapabilityRisk]) -> bool:
    high_risks = {
        MCPCapabilityRisk.WRITES_WORKSPACE,
        MCPCapabilityRisk.RUNS_COMMANDS,
        MCPCapabilityRisk.SECRET_ACCESS,
        MCPCapabilityRisk.EXTERNAL_AUTH,
        MCPCapabilityRisk.PROVIDER_NATIVE_EXECUTION,
        MCPCapabilityRisk.ARTIFACT_EXPORT,
    }
    return bool(risks.intersection(high_risks))


def _url_origin(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
