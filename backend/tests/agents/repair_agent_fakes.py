"""Shared fakes for the diff-aware repair agent tests (phase-30).

Not a test module (no ``test_`` prefix). The agent's four seams — model, tools, workspace/git and
the suite runner — are all injected, so a full attempt (patch → commit → re-run → delta) runs
deterministically without Docker, Mongo-side effects aside.
"""

from __future__ import annotations

from beanie import PydanticObjectId

from app.agents.anthropic_client import ToolUse, TurnComplete
from app.agents.cost import Usage
from app.core.errors import NotFoundError
from app.db.models import Project, TestRun
from app.sandbox.schemas import FileContent, FileNode, GitInfo
from app.testing.models import TestResult, TestScope, TestStatus
from tests.agents.test_client_tool_loop import FakeTransport, ScriptedTurn


def build_repair_transport(
    tool_uses: list[ToolUse], *, final_text: str = "Restored the empty-title guard."
) -> FakeTransport:
    """Script the single patch loop the agent runs: one tool turn, then a summary turn."""
    turns: list[ScriptedTurn] = []
    if tool_uses:
        turns.append(
            ScriptedTurn(
                deltas=["Patching…"],
                turn=TurnComplete(
                    text="Patching…",
                    tool_uses=tool_uses,
                    usage=Usage(300, 80),
                    stop_reason="tool_use",
                ),
            )
        )
    turns.append(
        ScriptedTurn(deltas=[final_text], turn=TurnComplete(text=final_text, usage=Usage(60, 20)))
    )
    return FakeTransport(turns)


def tool_use(name: str, args: dict[str, object]) -> ToolUse:
    return ToolUse(id=f"tu_{name}_{abs(hash(str(args))) % 9999}", name=name, input=args)


class FakeRepairWorkspace:
    """In-memory workspace: the FS surface the tools need + the git surface the agent needs."""

    def __init__(
        self,
        files: dict[str, str] | None = None,
        *,
        diff_text: str = "--- a/src.ts\n+++ b/src.ts\n",
        last_passing: str | None = "sha-green",
    ) -> None:
        self.files: dict[str, str] = dict(files or {})
        self.diff_text = diff_text
        self.last_passing = last_passing
        self.commits: list[str] = []
        self.diff_calls: list[dict[str, object]] = []
        self._sha = 0
        self._committed: dict[str, str] = {}

    # -- FS (tool-facing) --------------------------------------------------------------
    async def read(self, project: Project, path: str) -> FileContent:
        if path not in self.files:
            raise NotFoundError(f"File not found: {path}")
        content = self.files[path]
        return FileContent(path=path, content=content, size=len(content.encode("utf-8")))

    async def write(self, project: Project, path: str, content: str) -> FileNode:
        self.files[path] = content
        return FileNode(path=path, type="file", size=len(content.encode("utf-8")))

    async def tree(
        self, project: Project, path: str = ".", depth: int | None = None
    ) -> list[FileNode]:
        return [
            FileNode(path=p, type="file", size=len(c.encode("utf-8")))
            for p, c in sorted(self.files.items())
        ]

    # -- git (agent-facing) ------------------------------------------------------------
    async def commit(self, project: Project, message: str) -> tuple[str | None, bool]:
        if self.files == self._committed:
            return (f"sha{self._sha}" if self._sha else None), False
        self._sha += 1
        self._committed = dict(self.files)
        self.commits.append(message)
        return f"sha{self._sha}", True

    async def git_info(self, project: Project) -> GitInfo:
        return GitInfo(
            current_sha=f"sha{self._sha}" if self._sha else "sha0",
            last_passing=self.last_passing,
        )

    async def diff(
        self, project: Project, sha_a: str, sha_b: str, paths: list[str] | None = None
    ) -> str:
        self.diff_calls.append({"a": sha_a, "b": sha_b, "paths": list(paths or [])})
        return self.diff_text


class ScriptedRunner:
    """Fake TestRunner: returns queued TestRuns in order and records the scopes it was asked for."""

    def __init__(self, runs: list[TestRun]) -> None:
        self._runs = list(runs)
        self.scopes: list[str] = []

    async def run(
        self, project: Project, scope: TestScope = "all", name_filter: str | None = None
    ) -> TestRun:
        self.scopes.append(str(scope))
        return self._runs.pop(0) if len(self._runs) > 1 else self._runs[0]


# --------------------------------------------------------------------- result builders


def result(
    name: str,
    status: TestStatus,
    *,
    file: str | None = None,
    framework: str = "jest",
    criterion_id: str | None = None,
    refs: list[str] | None = None,
) -> TestResult:
    from app.testing.models import Failure

    failure = None
    if status is TestStatus.failed:
        failure = Failure(
            message="expected 400, received 201",
            assertion=name,
            stack=f"at ({(refs or [file or ''])[0]}:22:18)",
            files_referenced=list(refs or ([file] if file else [])),
        )
    return TestResult(
        name=name,
        status=status,
        framework=framework,
        criterion_id=criterion_id,
        file=file,
        failure=failure,
    )


async def make_project(name: str = "app") -> Project:
    return await Project(user_id=PydanticObjectId(), name=name, app_db_name="db").insert()


async def make_run(project: Project, results: list[TestResult]) -> TestRun:
    assert project.id is not None
    return await TestRun(
        project_id=project.id,
        results=[r.model_dump(mode="json") for r in results],
        failures=[r.model_dump(mode="json") for r in results if r.status is TestStatus.failed],
    ).insert()
