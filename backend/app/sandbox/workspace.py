"""Workspace orchestration surface (phase-12).

Binds a project to a :class:`~app.sandbox.runtime.WorkspaceRuntime`, guarantees the workspace is
git-backed on first access, and exposes the FS + git operations the router (and later the IDE /
agent tools) call. The *runtime provider* is an injectable module seam: production wires the
Docker backend via the sandbox manager; tests inject a local backend.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.config import get_config

# SystemError shadows the builtin (taxonomy name fixed by the plan); A004 suppressed on-line.
from app.core.errors import SystemError  # noqa: A004
from app.db.models import Project
from app.sandbox.fs import WorkspaceFs
from app.sandbox.git import WorkspaceGit
from app.sandbox.manager import get_manager
from app.sandbox.runtime import DockerRuntime, WorkspaceRuntime
from app.sandbox.schemas import FileContent, FileNode, GitInfo

RuntimeProvider = Callable[[Project], Awaitable[WorkspaceRuntime]]


async def _default_runtime_provider(project: Project) -> WorkspaceRuntime:
    """Production provider: ensure the container (touches idle activity) and wrap it."""
    manager = get_manager()
    await manager.ensure(project)
    container = await manager.get_container(str(project.id))
    if container is None:  # pragma: no cover - ensure() just created/started it
        raise SystemError("Sandbox container unavailable")
    return DockerRuntime(container)


_provider: RuntimeProvider | None = None


def set_runtime_provider(provider: RuntimeProvider | None) -> None:
    """Install a runtime provider (tests inject a local-filesystem backend)."""
    global _provider
    _provider = provider


def reset_runtime_provider() -> None:
    global _provider
    _provider = None


def active_runtime_provider() -> RuntimeProvider:
    """The effective provider (injected override, else the production Docker-backed one)."""
    return _provider or _default_runtime_provider


class WorkspaceService:
    def __init__(self, provider: RuntimeProvider | None = None) -> None:
        self._provider = provider

    async def _open(self, project: Project) -> tuple[WorkspaceFs, WorkspaceGit]:
        provider = self._provider or active_runtime_provider()
        runtime = await provider(project)
        git = WorkspaceGit(runtime)
        await git.ensure_init()  # every workspace is git-backed from first touch
        max_bytes = int(get_config().get("workspace_max_file_bytes"))
        fs = WorkspaceFs(runtime, str(project.id), max_bytes)
        return fs, git

    async def ensure_workspace(self, project: Project) -> None:
        await self._open(project)

    async def tree(
        self, project: Project, path: str = ".", depth: int | None = None
    ) -> list[FileNode]:
        fs, _ = await self._open(project)
        return await fs.list_tree(path, depth)

    async def read(self, project: Project, path: str) -> FileContent:
        fs, _ = await self._open(project)
        return await fs.read_file(path)

    async def write(self, project: Project, path: str, content: str) -> FileNode:
        fs, _ = await self._open(project)
        return await fs.write_file(path, content)

    async def delete(self, project: Project, path: str) -> None:
        fs, _ = await self._open(project)
        await fs.delete(path)

    async def mkdir(self, project: Project, path: str) -> FileNode:
        fs, _ = await self._open(project)
        return await fs.mkdir(path)

    async def move(self, project: Project, src: str, dst: str) -> FileNode:
        fs, _ = await self._open(project)
        return await fs.move(src, dst)

    async def commit(self, project: Project, message: str) -> tuple[str | None, bool]:
        _, git = await self._open(project)
        return await git.commit(message)

    async def git_info(self, project: Project) -> GitInfo:
        _, git = await self._open(project)
        return GitInfo(current_sha=await git.current_sha(), last_passing=await git.last_passing())

    async def restore(self, project: Project, sha: str) -> bool:
        """Reset the workspace back to ``sha`` (the repair loop's undo for a worsening patch)."""
        _, git = await self._open(project)
        return await git.restore(sha)

    async def set_last_passing(self, project: Project, sha: str) -> None:
        """Advance the ``last-passing`` ref — the anchor the repair loop diffs against."""
        _, git = await self._open(project)
        await git.set_last_passing(sha)

    async def diff(
        self, project: Project, sha_a: str, sha_b: str, paths: list[str] | None = None
    ) -> str:
        """Unified diff between two refs, optionally scoped to ``paths`` (phase-29)."""
        _, git = await self._open(project)
        return await git.diff(sha_a, sha_b, paths)

    async def changed_paths(self, project: Project, sha_a: str, sha_b: str) -> list[str]:
        """Workspace-relative paths that differ between two refs (phase-55 build repair)."""
        _, git = await self._open(project)
        return await git.changed_paths(sha_a, sha_b)
