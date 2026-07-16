"""Per-call tool context: the project sandbox + services every tool acts through (phase-21)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.db.models import Project, Run
from app.sandbox.exec import ExecOutcome, ExecService, get_exec_service
from app.sandbox.preview import PreviewService, get_preview_service
from app.sandbox.schemas import FileContent, FileNode, PreviewInfo
from app.sandbox.workspace import WorkspaceService


class WorkspaceApi(Protocol):
    """The slice of :class:`~app.sandbox.workspace.WorkspaceService` the tools use."""

    async def read(self, project: Project, path: str) -> FileContent: ...

    async def write(self, project: Project, path: str, content: str) -> FileNode: ...

    async def tree(
        self, project: Project, path: str = ".", depth: int | None = None
    ) -> list[FileNode]: ...

    async def commit(self, project: Project, message: str) -> tuple[str | None, bool]: ...


class ExecRunner(Protocol):
    """The slice of :class:`~app.sandbox.exec.ExecService` the tools use (tests inject a fake)."""

    async def run(
        self,
        project: Project,
        cmd: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ExecOutcome: ...


class PreviewRunner(Protocol):
    async def start(self, project: Project) -> PreviewInfo: ...

    async def restart(self, project: Project) -> PreviewInfo: ...


@dataclass
class ToolContext:
    """Everything a tool needs, all scoped to one project's sandbox."""

    project: Project
    run: Run
    workspace: WorkspaceApi
    exec_service: ExecRunner
    preview: PreviewRunner
    channel: str

    @classmethod
    def build(
        cls,
        project: Project,
        run: Run,
        *,
        workspace: WorkspaceApi | None = None,
        exec_service: ExecRunner | None = None,
        preview: PreviewRunner | None = None,
        channel: str | None = None,
    ) -> ToolContext:
        return cls(
            project=project,
            run=run,
            workspace=workspace or WorkspaceService(),
            exec_service=exec_service or get_exec_service(),
            preview=preview or get_preview_service(),
            channel=channel or str(project.id),
        )


__all__ = [
    "WorkspaceApi",
    "ExecRunner",
    "PreviewRunner",
    "ToolContext",
    "ExecService",
    "PreviewService",
]
