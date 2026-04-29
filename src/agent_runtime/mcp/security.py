from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlparse

from agent_runtime.mcp.trust import (
    MCPCapabilityRisk,
    MCPServerRiskProfile,
    effective_server_trust_policy,
    trust_fingerprint,
)

_SHELL_EXECUTABLES = {
    "bash",
    "bash.exe",
    "cmd",
    "cmd.exe",
    "fish",
    "fish.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "sh",
    "sh.exe",
    "zsh",
    "zsh.exe",
}


def validate_mcp_server_security(
    server_spec: object,
    *,
    production: bool = True,
) -> MCPServerRiskProfile:
    """Validate transport-level MCP security invariants and return a risk profile."""

    transport = str(getattr(server_spec, "transport", ""))
    if transport == "stdio":
        _validate_stdio_security(server_spec)
    elif transport in {"http", "sse", "streamable_http"}:
        _validate_http_security(server_spec, production=production)
    else:
        raise ValueError(f"Unsupported MCP transport: {transport!r}.")
    return build_server_risk_profile(server_spec)


def build_server_risk_profile(server_spec: object) -> MCPServerRiskProfile:
    transport = str(getattr(server_spec, "transport", ""))
    policy = effective_server_trust_policy(server_spec)
    url = getattr(server_spec, "url", None)
    command = getattr(server_spec, "command", None)
    args = tuple(str(arg) for arg in getattr(server_spec, "args", ()) or ())
    risks: set[MCPCapabilityRisk] = set()
    if transport == "stdio":
        risks.add(MCPCapabilityRisk.RUNS_COMMANDS)
        risks.add(MCPCapabilityRisk.STATEFUL_SESSION)
    if transport in {"http", "sse", "streamable_http"}:
        risks.add(MCPCapabilityRisk.NETWORK_EGRESS)
    if getattr(server_spec, "authorization", None) is not None or getattr(
        server_spec, "auth_provider_name", None
    ):
        risks.add(MCPCapabilityRisk.EXTERNAL_AUTH)
    headers = getattr(server_spec, "headers", None)
    if isinstance(headers, dict) and any(key.lower() == "authorization" for key in headers):
        risks.add(MCPCapabilityRisk.EXTERNAL_AUTH)
    if getattr(server_spec, "allow_provider_native", False):
        risks.add(MCPCapabilityRisk.PROVIDER_NATIVE_EXECUTION)
    if getattr(server_spec, "allowed_roots", ()):
        risks.add(MCPCapabilityRisk.READS_WORKSPACE)
    metadata = getattr(server_spec, "metadata", {})
    package_name = metadata.get("package_name") if isinstance(metadata, dict) else None
    package_version = metadata.get("package_version") if isinstance(metadata, dict) else None
    package_digest = metadata.get("package_digest") if isinstance(metadata, dict) else None
    return MCPServerRiskProfile(
        server=str(getattr(server_spec, "name", "")),
        transport=transport,
        source="local" if transport == "stdio" else "remote",
        command=(str(command), *args) if command else (),
        cwd=getattr(server_spec, "cwd", None),
        url_origin=_url_origin(url),
        protocol_version=getattr(server_spec, "protocol_version", None),
        auth_mode=_auth_mode(server_spec),
        requested_scopes=tuple(sorted(policy.allowed_scopes)),
        declared_tools=tuple(sorted(policy.allowed_tools or ())),
        capability_risks=frozenset(risks),
        package_name=str(package_name) if package_name is not None else None,
        package_version=str(package_version) if package_version is not None else None,
        package_digest=str(package_digest) if package_digest is not None else None,
        trust_fingerprint=trust_fingerprint(server_spec),
    )


def _validate_stdio_security(server_spec: object) -> None:
    command = getattr(server_spec, "command", None)
    if not isinstance(command, str) or not command:
        raise ValueError(f"stdio MCP server '{getattr(server_spec, 'name', '')}' requires command.")
    if command.strip() != command or "\r" in command or "\n" in command:
        raise ValueError("stdio MCP command must be a single executable path/name.")
    if _contains_unresolved_whitespace(command):
        raise ValueError("stdio MCP command must not be a shell-style command string.")
    executable_name = Path(command).name.lower()
    if executable_name in _SHELL_EXECUTABLES:
        raise ValueError("stdio MCP command must not launch through a shell executable.")
    cwd = getattr(server_spec, "cwd", None)
    roots = tuple(str(root) for root in getattr(server_spec, "allowed_roots", ()) or ())
    if cwd and roots:
        resolved_cwd = Path(cwd).resolve()
        if not any(_is_relative_to(resolved_cwd, Path(root).resolve()) for root in roots):
            raise ValueError("stdio MCP cwd must be inside an allowed root.")


def _validate_http_security(server_spec: object, *, production: bool) -> None:
    url = getattr(server_spec, "url", None)
    transport = str(getattr(server_spec, "transport", ""))
    if not isinstance(url, str) or not url:
        raise ValueError(f"{transport} MCP server '{getattr(server_spec, 'name', '')}' requires url.")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("HTTP MCP server URL must use http or https.")
    if production and parsed.scheme != "https":
        raise ValueError("HTTP MCP server URL must use https in production mode.")
    host = parsed.hostname or ""
    if _is_private_or_reserved_host(host) and not _dev_private_ip_override(server_spec):
        raise ValueError("HTTP MCP server URL targets a private/reserved host.")
    allowed_domains = tuple(
        str(domain).lower()
        for domain in (
            *(getattr(server_spec, "allowed_egress_domains", ()) or ()),
            *effective_server_trust_policy(server_spec).allowed_domains,
        )
    )
    if allowed_domains and not _host_allowed(host.lower(), allowed_domains):
        raise ValueError("HTTP MCP server host is outside allowed egress domains.")


def _contains_unresolved_whitespace(command: str) -> bool:
    if not any(char.isspace() for char in command):
        return False
    if os.path.isabs(command) and Path(command).exists():
        return False
    return True


def _is_private_or_reserved_host(host: str) -> bool:
    lowered = host.lower()
    if lowered in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        return False
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def _dev_private_ip_override(server_spec: object) -> bool:
    metadata = getattr(server_spec, "metadata", {})
    if isinstance(metadata, dict) and metadata.get("allow_private_ip") is True:
        return True
    url = getattr(server_spec, "url", None)
    return bool(getattr(server_spec, "allow_remote_http", False)) and bool(
        isinstance(url, str) and urlparse(url).scheme == "https"
    )


def _host_allowed(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _url_origin(url: object) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _auth_mode(server_spec: object) -> str | None:
    if getattr(server_spec, "authorization", None) is not None:
        return "authorization"
    if getattr(server_spec, "auth_provider_name", None):
        return "auth_provider"
    headers = getattr(server_spec, "headers", None)
    if isinstance(headers, dict) and any(key.lower() == "authorization" for key in headers):
        return "authorization_header"
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
