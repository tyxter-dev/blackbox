from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.core.artifacts import ArtifactRef
from agent_runtime.core.errors import UnsupportedFeatureError, WorkspaceError
from agent_runtime.workspaces import (
    CloudWorkspaceProvider,
    DockerWorkspaceProvider,
    FakeSandboxClient,
    LocalWorkspaceProvider,
    SandboxWorkspaceProvider,
    WorkspacePort,
    WorkspaceProviderCapabilities,
    WorkspaceSpec,
    workspace_state_from_dict,
    workspace_state_to_dict,
)


def test_workspace_provider_capabilities_defaults_are_false() -> None:
    capabilities = WorkspaceProviderCapabilities()

    assert capabilities.supports_local_files is False
    assert capabilities.supports_sandbox is False
    assert capabilities.supports_docker is False
    assert capabilities.supports_ports is False


async def test_local_workspace_capabilities_are_honest(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider()

    capabilities = provider.capabilities()
    assert capabilities.supports_local_files is True
    assert capabilities.supports_sandbox is False
    assert capabilities.supports_restore is True
    assert capabilities.supports_ports is False

    ws = await provider.open(WorkspaceSpec.local(tmp_path))
    with pytest.raises(UnsupportedFeatureError):
        await provider.expose_port(ws, 8000)
    with pytest.raises(WorkspaceError):
        await provider.open(WorkspaceSpec.sandbox())


async def test_sandbox_workspace_capabilities_are_honest() -> None:
    provider = SandboxWorkspaceProvider(FakeSandboxClient())

    capabilities = provider.capabilities()
    assert capabilities.supports_sandbox is True
    assert capabilities.supports_commands is True
    assert capabilities.supports_restore is True
    assert capabilities.supports_ports is True

    with pytest.raises(UnsupportedFeatureError):
        await provider.open(WorkspaceSpec.local("/tmp/nope"))


def test_docker_workspace_provider_uses_docker_kind_contract() -> None:
    provider = DockerWorkspaceProvider(image="python:3.12")

    capabilities = provider.capabilities()
    assert capabilities.supports_docker is True
    assert capabilities.supports_sandbox is False
    assert WorkspaceSpec.docker(image="python:3.12").kind == "docker"


async def test_cloud_workspace_provider_opens_opaque_ref() -> None:
    provider = CloudWorkspaceProvider()

    ws = await provider.open(WorkspaceSpec.cloud(provider_workspace_id="cloud_ws_1"))

    assert ws.kind == "cloud"
    assert ws.provider == "cloud-workspace"
    assert ws.provider_workspace_id == "cloud_ws_1"
    assert provider.capabilities().supports_cloud_refs is True


async def test_workspace_session_state_round_trips(tmp_path: Path) -> None:
    provider = LocalWorkspaceProvider()
    ws = await provider.open(WorkspaceSpec.local(tmp_path))

    payload = workspace_state_to_dict(provider.session_state(ws))
    restored = workspace_state_from_dict(payload)

    assert restored.workspace_id == ws.id
    assert restored.provider == "local-workspace"
    assert restored.root == ws.root


async def test_unsupported_restore_raises_typed_error_for_unknown_snapshot() -> None:
    provider = SandboxWorkspaceProvider(FakeSandboxClient())

    with pytest.raises(WorkspaceError):
        await provider.restore(ArtifactRef(id="missing"))


def test_workspace_port_contract() -> None:
    port = WorkspacePort(workspace_id="ws_1", port=3000, url="http://127.0.0.1:3000")

    assert port.id.startswith("port_")
    assert port.protocol == "http"
