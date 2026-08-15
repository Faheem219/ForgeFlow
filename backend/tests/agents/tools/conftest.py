"""Shared fixtures for the sandbox-bound tool tests (phase-21).

Tools are thin adapters over the sandbox services, so they're tested against fakes (the plan's "mock
sandbox"): an in-memory workspace, and injected exec/preview stubs. This runs anywhere — the real
FS/git/exec runtimes are covered by the phase-12/13 sandbox suite. Path safety is a **tool-level**
guardrail (lexical, via app.sandbox.paths), so it is exercised here without a runtime.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import NotFoundError
from app.db.blobs import get_blob_store
from app.db.models import Project, Run
from app.sandbox.exec import ExecOutcome
from app.sandbox.schemas import FileContent, FileNode, GitInfo, PreviewInfo, PreviewStatus


@pytest.fixture
def blob_env(monkeypatch: pytest.MonkeyPatch, tmp_path: str) -> Iterator[None]:
    # Filesystem blobs so captured exec output never touches the shared meta DB.
    monkeypatch.setenv("BLOB_BACKEND", "filesystem")
    monkeypatch.setenv("BLOB_FS_DIR", str(tmp_path))
    reset_config()
    yield


@pytest_asyncio.fixture
async def project_run() -> tuple[Project, Run]:
    project = await Project(user_id=PydanticObjectId(), name="p", app_db_name="db").insert()
    run = await Run(project_id=project.id, kind="agent").insert()
    return project, run


class FakeWorkspace:
    """In-memory stand-in for WorkspaceService (path safety is enforced by the tools, lexically)."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.commits: list[str] = []  # every commit message, in order (phase-56 asserts on these)
        self._committed: dict[str, str] = {}
        self._sha = 0

    async def write(self, project: Project, path: str, content: str) -> FileNode:
        self.files[path] = content
        return FileNode(path=path, type="file", size=len(content.encode("utf-8")))

    async def read(self, project: Project, path: str) -> FileContent:
        if path not in self.files:
            raise NotFoundError("File not found")
        content = self.files[path]
        return FileContent(path=path, content=content, size=len(content.encode("utf-8")))

    async def tree(
        self, project: Project, path: str = ".", depth: int | None = None
    ) -> list[FileNode]:
        prefix = "" if path in (".", "") else path.rstrip("/") + "/"
        return [
            FileNode(path=p, type="file", size=len(c.encode("utf-8")))
            for p, c in sorted(self.files.items())
            if p.startswith(prefix)
        ]

    async def commit(self, project: Project, message: str) -> tuple[str | None, bool]:
        self.commits.append(message)
        if self.files == self._committed:
            return (f"sha{self._sha}" if self._sha else None), False
        self._sha += 1
        self._committed = dict(self.files)
        return f"sha{self._sha}", True

    async def git_info(self, project: Project) -> GitInfo:  # used by tests, not the tools
        return GitInfo(current_sha=f"sha{self._sha}" if self._sha else None, last_passing=None)


class FakeExec:
    """Stand-in for ExecService: records calls, captures canned output to the blob store."""

    def __init__(self, exit_code: int = 0, output: bytes = b"OK") -> None:
        self.calls: list[dict[str, object]] = []
        self.exit_code = exit_code
        self.output = output

    async def run(
        self,
        project: Project,
        cmd: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ExecOutcome:
        self.calls.append({"cmd": list(cmd), "cwd": cwd, "timeout": timeout})
        ref = await get_blob_store().put(self.output)
        return ExecOutcome(
            exec_id="e1",
            exit_code=self.exit_code,
            duration_s=0.0,
            timed_out=False,
            cancelled=False,
            output_ref=ref,
        )


class FakePreview:
    def __init__(self) -> None:
        self.started = 0
        self.restarted = 0

    async def start(self, project: Project) -> PreviewInfo:
        self.started += 1
        return PreviewInfo(
            project_id=str(project.id),
            fe_url="http://preview",
            fe_status=PreviewStatus.running,
            be_status=PreviewStatus.running,
        )

    async def restart(self, project: Project) -> PreviewInfo:
        self.restarted += 1
        return PreviewInfo(
            project_id=str(project.id),
            fe_status=PreviewStatus.running,
            be_status=PreviewStatus.running,
        )
