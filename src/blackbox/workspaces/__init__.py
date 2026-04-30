from blackbox.workspaces.changes import (
    CommandResult,
    CommandSpec,
    FileChange,
    Patch,
    PatchArtifact,
)
from blackbox.workspaces.cloud import CloudWorkspaceProvider
from blackbox.workspaces.docker import DockerSandboxClient, DockerWorkspaceProvider
from blackbox.workspaces.fake import FakeSandboxClient
from blackbox.workspaces.git import GitWorkspaceProvider
from blackbox.workspaces.local import LocalWorkspaceProvider
from blackbox.workspaces.provider import (
    PendingWorkspaceOperation,
    WorkspaceApprovalRequired,
    WorkspaceProvider,
)
from blackbox.workspaces.runtime import CommandExecutor, WorkspaceRuntime
from blackbox.workspaces.sandbox import SandboxWorkspaceProvider
from blackbox.workspaces.sandbox_client import (
    SandboxClient,
    SandboxCommandEvent,
    SandboxSession,
)
from blackbox.workspaces.serialization import (
    workspace_ref_from_dict,
    workspace_ref_to_dict,
    workspace_state_from_dict,
    workspace_state_to_dict,
)
from blackbox.workspaces.spec import (
    WorkspaceMount,
    WorkspacePort,
    WorkspaceProviderCapabilities,
    WorkspaceRef,
    WorkspaceSessionState,
    WorkspaceSpec,
)

__all__ = [
    "CloudWorkspaceProvider",
    "CommandExecutor",
    "CommandResult",
    "CommandSpec",
    "DockerSandboxClient",
    "DockerWorkspaceProvider",
    "FakeSandboxClient",
    "FileChange",
    "GitWorkspaceProvider",
    "LocalWorkspaceProvider",
    "Patch",
    "PatchArtifact",
    "PendingWorkspaceOperation",
    "SandboxClient",
    "SandboxCommandEvent",
    "SandboxSession",
    "SandboxWorkspaceProvider",
    "WorkspaceApprovalRequired",
    "WorkspaceMount",
    "WorkspacePort",
    "WorkspaceProvider",
    "WorkspaceProviderCapabilities",
    "WorkspaceRef",
    "WorkspaceRuntime",
    "WorkspaceSessionState",
    "WorkspaceSpec",
    "workspace_ref_from_dict",
    "workspace_ref_to_dict",
    "workspace_state_from_dict",
    "workspace_state_to_dict",
]
