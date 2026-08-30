"""The build-repair widening analyzer guarantees a non-empty editable set (phase-55 task 11).

When the failure names no editable source directly (a boot crash whose stack is all node_modules),
the wrapper widens the target set — first from the files this build wrote, then from what changed
since the last passing run — always inside the whole-context budget. With nothing to widen from, it
returns an empty editable set so the controller's pre-check can escalate cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.agents.build_context import BuildRepairContextAnalyzer, _editable
from app.agents.repair_context import RepairContextAnalyzer
from app.core.config import reset_config
from app.core.errors import NotFoundError
from app.sandbox.schemas import FileContent, GitInfo

if TYPE_CHECKING:  # the stubs below are structural stand-ins; nothing here touches a DB
    from app.db.models import Project, TestRun

SPEC = "frontend/e2e/home.spec.ts"  # a *test* file — implicated, but never editable
WROTE = "frontend/src/features/todos/TodoPage.tsx"
CHANGED = "backend/src/features/todos/todos.controller.ts"


@dataclass
class _FakeWorkspace:
    """Serves file contents + a git view; records nothing the base analyzer cannot ask for."""

    files: dict[str, str] = field(default_factory=dict)
    last_passing: str | None = "sha-old"
    current: str | None = "sha-new"
    changed: list[str] = field(default_factory=list)

    async def read(self, project: Any, path: str) -> FileContent:
        if path not in self.files:
            raise NotFoundError(f"not found: {path}")
        return FileContent(path=path, content=self.files[path], size=len(self.files[path]))

    async def git_info(self, project: Any) -> GitInfo:
        return GitInfo(current_sha=self.current, last_passing=self.last_passing)

    async def diff(self, project: Any, a: str, b: str, paths: list[str] | None = None) -> str:
        return ""

    async def changed_paths(self, project: Any, a: str, b: str) -> list[str]:
        return list(self.changed)


class _StubRun:
    """A TestRun stand-in: the analyzer only reads `.failures` / `.results` (persist is skipped)."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.failures = [r for r in results if r.get("status") == "failed"]


def _boot_failure_naming_only_a_test_file() -> _StubRun:
    # files_referenced is empty (node_modules stripped upstream); only the spec file is implicated.
    return _StubRun(
        [
            {
                "name": "boot",
                "status": "failed",
                "framework": "boot",
                "file": SPEC,
                "failure": {
                    "message": "crash",
                    "stack": "at node_modules/x",
                    "files_referenced": [],
                },
            }
        ]
    )


def _analyzer(ws: _FakeWorkspace, *, fallback: list[str]) -> BuildRepairContextAnalyzer:
    base = RepairContextAnalyzer(workspace=ws)
    return BuildRepairContextAnalyzer(base, fallback_paths=fallback, workspace=ws)


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_config()
    yield
    reset_config()


async def test_widens_from_fallback_when_only_a_test_file_is_implicated() -> None:
    ws = _FakeWorkspace(files={SPEC: "spec", WROTE: "export const x = 1"})
    analyzer = _analyzer(ws, fallback=[WROTE])

    context = await analyzer.analyze(
        cast("Project", object()),
        cast("TestRun", _boot_failure_naming_only_a_test_file()),
        persist=False,
    )

    assert _editable(context) == [WROTE]  # the build-authored file became the repair target
    assert any("Widened" in note for note in context.notes)


async def test_widens_from_changed_paths_when_no_fallback() -> None:
    ws = _FakeWorkspace(files={SPEC: "spec", CHANGED: "export const y = 2"}, changed=[CHANGED])
    analyzer = _analyzer(ws, fallback=[])

    context = await analyzer.analyze(
        cast("Project", object()),
        cast("TestRun", _boot_failure_naming_only_a_test_file()),
        persist=False,
    )

    assert _editable(context) == [CHANGED]


async def test_nothing_to_widen_from_leaves_the_set_empty_for_the_precheck() -> None:
    ws = _FakeWorkspace(files={SPEC: "spec"}, last_passing=None, changed=[])
    analyzer = _analyzer(ws, fallback=[])

    context = await analyzer.analyze(
        cast("Project", object()),
        cast("TestRun", _boot_failure_naming_only_a_test_file()),
        persist=False,
    )

    assert _editable(context) == []  # the controller's pre-check escalates on this


async def test_widening_respects_the_context_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPAIR_CONTEXT_MAX_CHARS", "200")
    reset_config()
    big = "x" * 500
    ws = _FakeWorkspace(files={SPEC: "spec", WROTE: big})
    analyzer = _analyzer(ws, fallback=[WROTE])

    context = await analyzer.analyze(
        cast("Project", object()),
        cast("TestRun", _boot_failure_naming_only_a_test_file()),
        persist=False,
    )

    # The oversized fallback file is refused by the budget, so the set stays empty AND within cap —
    # widening can never become "feed the whole repo".
    assert _editable(context) == []
    assert context.size() <= 200
