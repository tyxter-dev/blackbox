from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from blackbox.core.approvals import ApprovalDecision
from blackbox.core.artifacts import Artifact, ArtifactPage, ArtifactRef
from blackbox.core.errors import ApprovalError, ConfigurationError
from blackbox.core.events import AgentEvent


@dataclass(slots=True)
class WorkspaceRuntimeFacade:
    """Provider registry and lifecycle facade for first-class workspaces."""

    providers: dict[str, Any] = field(default_factory=dict)
    _kind_providers: dict[str, str] = field(default_factory=dict)
    _workspace_providers: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from blackbox.workspaces import (
            CloudWorkspaceProvider,
            GitWorkspaceProvider,
            LocalWorkspaceProvider,
        )

        self.register(LocalWorkspaceProvider(), kinds=["local"])
        self.register(GitWorkspaceProvider(), kinds=["git"])
        self.register(CloudWorkspaceProvider(), kinds=["cloud"])

    def register(self, provider: Any, *, kinds: list[str] | None = None) -> Any:
        provider_id = str(getattr(provider, "provider_id", type(provider).__name__))
        self.providers[provider_id] = provider
        for kind in kinds or self._infer_kinds(provider):
            self._kind_providers[kind] = provider_id
        return provider

    async def open(
        self,
        spec: Any,
        *,
        provider: str | Any | None = None,
        policy: Any = None,
    ) -> Any:
        from blackbox.workspaces import WorkspaceRef, WorkspaceSpec

        if isinstance(spec, WorkspaceRef):
            workspace_provider = self.provider_for(spec, provider=provider)
            self._workspace_providers[spec.id] = workspace_provider
            return spec
        if not isinstance(spec, WorkspaceSpec):
            raise ConfigurationError("runtime.workspaces.open requires a WorkspaceSpec.")
        workspace_provider = self._provider_for_spec(spec, provider=provider, policy=policy)
        ws = await workspace_provider.open(spec)
        self._workspace_providers[ws.id] = workspace_provider
        self.providers.setdefault(workspace_provider.provider_id, workspace_provider)
        return ws

    async def attach(
        self,
        state: Any,
        *,
        provider: str | Any | None = None,
        policy: Any = None,
    ) -> Any:
        workspace_provider = self._provider_for_kind(
            state.kind,
            provider=provider or state.provider,
            policy=policy,
        )
        ws = await workspace_provider.attach(state)
        self._workspace_providers[ws.id] = workspace_provider
        return ws

    async def resolve(
        self,
        workspace: Any | None,
        *,
        provider: str | Any | None = None,
        policy: Any = None,
    ) -> tuple[Any | None, Any | None, bool]:
        from blackbox.workspaces import WorkspaceRef, WorkspaceSpec

        if workspace is None:
            return None, None, False
        if isinstance(workspace, WorkspaceRef):
            workspace_provider = self.provider_for(workspace, provider=provider)
            self._workspace_providers[workspace.id] = workspace_provider
            return workspace, workspace_provider, False
        if isinstance(workspace, WorkspaceSpec):
            ws = await self.open(workspace, provider=provider, policy=policy)
            return ws, self.provider_for(ws), True
        raise ConfigurationError("workspace must be a WorkspaceSpec or WorkspaceRef.")

    def provider_for(self, workspace: Any, *, provider: str | Any | None = None) -> Any:
        if provider is not None:
            return self._coerce_provider(provider)
        workspace_id = getattr(workspace, "id", None)
        if isinstance(workspace_id, str) and workspace_id in self._workspace_providers:
            return self._workspace_providers[workspace_id]
        provider_id = getattr(workspace, "provider", None)
        if isinstance(provider_id, str) and provider_id in self.providers:
            return self.providers[provider_id]
        raise ConfigurationError(
            "workspace provider is unknown; open the workspace through runtime.workspaces "
            "or pass workspace_provider explicitly."
        )

    async def close(self, workspace: Any, *, delete: bool = False) -> None:
        provider = self.provider_for(workspace)
        await provider.close(workspace, delete=delete)
        self._workspace_providers.pop(workspace.id, None)

    async def read_file(self, workspace: Any, path: str) -> str:
        return cast(str, await self.provider_for(workspace).read_file(workspace, path))

    async def write_file(self, workspace: Any, path: str, content: str) -> Any:
        return await self.provider_for(workspace).write_file(workspace, path, content)

    async def delete_file(self, workspace: Any, path: str) -> Any:
        return await self.provider_for(workspace).delete_file(workspace, path)

    async def list_files(
        self,
        workspace: Any,
        *,
        path: str = ".",
        recursive: bool = False,
        limit: int = 1000,
    ) -> list[str]:
        return cast(
            list[str],
            await self.provider_for(workspace).list_files(
                workspace,
                path=path,
                recursive=recursive,
                limit=limit,
            ),
        )

    async def apply_patch(self, workspace: Any, patch: Any) -> Any:
        return await self.provider_for(workspace).apply_patch(workspace, patch)

    async def run_command(self, workspace: Any, spec: Any) -> Any:
        return await self.provider_for(workspace).run_command(workspace, spec)

    async def snapshot(self, workspace: Any, *, name: str | None = None) -> Artifact:
        return cast(Artifact, await self.provider_for(workspace).snapshot(workspace, name=name))

    async def list_artifacts(
        self,
        workspace: Any,
        *,
        type: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> ArtifactPage:
        return cast(
            ArtifactPage,
            await self.provider_for(workspace).list_artifacts(
                workspace,
                type=type,
                after=after,
                limit=limit,
            ),
        )

    async def export_artifact(self, workspace: Any, ref: ArtifactRef) -> Artifact:
        return cast(Artifact, await self.provider_for(workspace).export_artifact(workspace, ref))

    async def approve(self, approval_id: str, decision: ApprovalDecision) -> None:
        last_error: Exception | None = None
        providers = [*self.providers.values(), *self._workspace_providers.values()]
        seen: set[int] = set()
        for provider in providers:
            identity = id(provider)
            if identity in seen:
                continue
            seen.add(identity)
            approve = getattr(provider, "approve", None)
            if not callable(approve):
                continue
            try:
                await approve(approval_id, decision)
                return
            except Exception as exc:  # pragma: no cover - provider-specific miss
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ApprovalError(f"No pending approval with id '{approval_id}'.")

    def drain_events(self, workspace: Any | None = None) -> list[AgentEvent]:
        providers = (
            [self.provider_for(workspace)]
            if workspace is not None
            else [*self.providers.values(), *self._workspace_providers.values()]
        )
        events: list[AgentEvent] = []
        seen: set[int] = set()
        for provider in providers:
            identity = id(provider)
            if identity in seen:
                continue
            seen.add(identity)
            drain = getattr(provider, "drain_events", None)
            if callable(drain):
                events.extend(drain())
        return events

    def _provider_for_spec(self, spec: Any, *, provider: str | Any | None, policy: Any) -> Any:
        return self._provider_for_kind(spec.kind, provider=provider, policy=policy, spec=spec)

    def _provider_for_kind(
        self,
        kind: str,
        *,
        provider: str | Any | None = None,
        policy: Any = None,
        spec: Any | None = None,
    ) -> Any:
        if provider is not None:
            return self._coerce_provider(provider)
        if policy is not None:
            fresh = self._fresh_provider(kind, policy=policy, spec=spec)
            if fresh is not None:
                return fresh
        provider_id = self._kind_providers.get(kind)
        if provider_id is None:
            raise ConfigurationError(
                f"workspace_provider is required for {kind!r} workspaces."
            )
        return self.providers[provider_id]

    def _coerce_provider(self, provider: str | Any) -> Any:
        if isinstance(provider, str):
            try:
                return self.providers[provider]
            except KeyError as exc:
                raise ConfigurationError(f"Unknown workspace provider {provider!r}.") from exc
        self.register(provider)
        return provider

    def _fresh_provider(self, kind: str, *, policy: Any, spec: Any | None) -> Any | None:
        from blackbox.workspaces import (
            CloudWorkspaceProvider,
            GitWorkspaceProvider,
            LocalWorkspaceProvider,
        )

        if kind == "local":
            return LocalWorkspaceProvider(policy=policy)
        if kind == "git":
            return GitWorkspaceProvider(policy=policy)
        if kind == "cloud":
            return CloudWorkspaceProvider(policy=policy)
        if kind == "docker" and spec is not None and getattr(spec, "image", None):
            from blackbox.workspaces import DockerWorkspaceProvider

            return DockerWorkspaceProvider(image=spec.image, policy=policy)
        return None

    def _infer_kinds(self, provider: Any) -> list[str]:
        capabilities = provider.capabilities() if callable(getattr(provider, "capabilities", None)) else None
        kinds: list[str] = []
        if getattr(capabilities, "supports_local_files", False):
            kinds.append("local")
        if getattr(capabilities, "supports_git_sources", False):
            kinds.append("git")
        if getattr(capabilities, "supports_sandbox", False):
            kinds.append("sandbox")
        if getattr(capabilities, "supports_docker", False):
            kinds.append("docker")
        if getattr(capabilities, "supports_cloud_refs", False):
            kinds.append("cloud")
        return kinds
