from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from blackbox.core.errors import ProviderExecutionError, WorkspaceError
from blackbox.core.events import EventTypes
from blackbox.workspaces.local import LocalWorkspaceProvider, _is_directory
from blackbox.workspaces.serialization import workspace_state_to_dict
from blackbox.workspaces.spec import (
    WorkspaceProviderCapabilities,
    WorkspaceRef,
    WorkspaceSessionState,
    WorkspaceSpec,
)


@dataclass
class GitWorkspaceProvider(LocalWorkspaceProvider):
    """Workspace provider that materializes a git ref into a local worktree."""

    provider_id: str = "git-workspace"
    git_bin: str = "git"

    def capabilities(self) -> WorkspaceProviderCapabilities:
        base = super().capabilities()
        return replace(base, supports_git_sources=True)

    async def open(self, spec: WorkspaceSpec) -> WorkspaceRef:
        """Clone the requested git source, open it as a local workspace, and record session state."""
        if spec.kind != "git":
            raise WorkspaceError(
                f"GitWorkspaceProvider supports only 'git' workspaces, got {spec.kind!r}."
            )
        repo = spec.repo or spec.url
        if not repo:
            raise WorkspaceError("Git workspace requires repo or url.")
        ref = spec.branch or spec.ref
        await self._policy_gate(
            "before_workspace_open",
            workspace_id=None,
            operation="open",
            action=repo,
            arguments={"kind": spec.kind, "repo": repo, "ref": ref},
        )
        root = Path(spec.root) if spec.root is not None else Path(tempfile.mkdtemp(prefix="blackbox-git-"))
        await asyncio.to_thread(root.parent.mkdir, parents=True, exist_ok=True)
        await _clone_repo(self.git_bin, repo, root, ref=ref)
        resolved_root = await asyncio.to_thread(lambda: str(root.resolve()))
        ref_obj = WorkspaceRef(
            kind="git",
            provider=self.provider_id,
            root=resolved_root,
            provider_workspace_id=repo,
            name=Path(repo.rstrip("/\\")).name or "git-workspace",
            metadata={
                "repo": repo,
                "ref": ref,
                "branch": spec.branch,
                **dict(spec.metadata),
            },
        )
        state = self.session_state(ref_obj)
        ref_obj = replace(ref_obj, session_state=state)
        self._open[ref_obj.id] = ref_obj
        self._emit(
            EventTypes.WORKSPACE_OPENED,
            ws=ref_obj,
            data={
                "root": ref_obj.root,
                "repo": repo,
                "ref": ref,
                "session_state": workspace_state_to_dict(state),
            },
        )
        return ref_obj

    async def attach(self, state: WorkspaceSessionState) -> WorkspaceRef:
        if state.kind != "git":
            raise WorkspaceError(f"Cannot attach non-git workspace state: {state.kind!r}.")
        if not state.root:
            raise WorkspaceError("Git workspace state requires a root path.")
        root = Path(state.root)
        if not await asyncio.to_thread(_is_directory, root):
            raise WorkspaceError(f"Git workspace root does not exist: {root}")
        ref_obj = WorkspaceRef(
            id=state.workspace_id,
            kind="git",
            provider=self.provider_id,
            root=await asyncio.to_thread(lambda: str(root.resolve())),
            provider_workspace_id=state.provider_workspace_id,
            provider_session_id=state.provider_session_id,
            session_state=state,
            metadata=dict(state.metadata),
        )
        self._open[ref_obj.id] = ref_obj
        self._emit(
            EventTypes.WORKSPACE_OPENED,
            ws=ref_obj,
            data={"root": ref_obj.root, "attached": True, "session_state": workspace_state_to_dict(state)},
        )
        return ref_obj

    def session_state(self, ws: WorkspaceRef) -> WorkspaceSessionState:
        if ws.root is None:
            raise WorkspaceError("Git workspace has no root to serialize.")
        return WorkspaceSessionState(
            provider=self.provider_id,
            workspace_id=ws.id,
            kind="git",
            provider_workspace_id=ws.provider_workspace_id,
            provider_session_id=ws.provider_session_id,
            root=ws.root,
            serialized_state={
                "repo": ws.metadata.get("repo"),
                "ref": ws.metadata.get("ref"),
            },
            metadata=dict(ws.metadata),
        )


async def _clone_repo(
    git_bin: str,
    repo: str,
    root: Path,
    *,
    ref: str | None,
) -> None:
    proc = await asyncio.create_subprocess_exec(
        git_bin,
        "clone",
        repo,
        str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    if proc.returncode != 0:
        raise ProviderExecutionError(
            "Git clone failed: "
            f"{stderr_b.decode(errors='replace') or stdout_b.decode(errors='replace')}"
        )
    if ref is None:
        return
    checkout = await asyncio.create_subprocess_exec(
        git_bin,
        "-C",
        str(root),
        "checkout",
        ref,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await checkout.communicate()
    if checkout.returncode != 0:
        raise ProviderExecutionError(
            "Git checkout failed: "
            f"{stderr_b.decode(errors='replace') or stdout_b.decode(errors='replace')}"
        )
