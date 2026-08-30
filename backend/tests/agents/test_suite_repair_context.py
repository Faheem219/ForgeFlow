"""The Test-stage repair context always hands the agent something to patch.

The regression these pin down, observed on a real project: an assertion failure's stack names only
its own spec, the spec is read-only during repair, so the editable set came back empty and pressing
**Repair** escalated on iteration 1 having changed no code. See ``app/agents/suite_context.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.agents.repair_context import RepairContext, RepairContextAnalyzer, editable_paths
from app.agents.suite_context import (
    SuiteRepairContextAnalyzer,
    companion_sources,
    is_source_path,
    spec_stem,
    suite_sources,
)
from app.core.config import reset_config
from app.core.errors import NotFoundError
from app.sandbox.schemas import FileContent, FileNode, GitInfo

if TYPE_CHECKING:  # the stubs below are structural stand-ins; nothing here touches a DB
    from app.db.models import Project, TestRun

SPEC = "backend/src/features/todos/todos.test.ts"
CONTROLLER = "backend/src/features/todos/todos.controller.ts"
SERVICE = "backend/src/features/todos/todos.service.ts"
SAME_STEM = "backend/src/features/todos/todos.ts"
ELSEWHERE = "backend/src/features/billing/invoices.service.ts"
E2E = "frontend/e2e/home.spec.ts"
PAGE = "frontend/src/pages/HomePage.tsx"
# The reported case (2026-08-30): the generated frontend spec sits one directory away from the
# component it renders, so nothing beside it can contain the bug.
APP_SPEC = "frontend/src/App.test.tsx"
MAIN = "frontend/src/main.tsx"
ROUTES = "frontend/src/routes.tsx"


@dataclass
class _FakeWorkspace:
    """Serves file contents, a git view, and a workspace listing."""

    files: dict[str, str] = field(default_factory=dict)
    last_passing: str | None = None
    current: str | None = "sha-new"
    changed: list[str] = field(default_factory=list)
    tree_fails: bool = False

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

    async def tree(self, project: Any, path: str = ".", depth: int | None = None) -> list[FileNode]:
        if self.tree_fails:
            raise RuntimeError("docker is down")
        return [FileNode(path=p, type="file", size=len(c)) for p, c in sorted(self.files.items())]


class _StubRun:
    """A TestRun stand-in: the analyzer only reads `.failures` / `.results` (persist is skipped)."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.failures = [r for r in results if r.get("status") == "failed"]


def _assertion_failure(name: str, spec: str, *, refs: list[str] | None = None) -> dict[str, Any]:
    """The shape that broke Repair: the stack points at the spec, never at the source."""
    return {
        "name": name,
        "status": "failed",
        "framework": "jest",
        "file": spec,
        "failure": {
            "message": "expected 400, received 201",
            "stack": f"at Object.<anonymous> ({spec}:22:18)",
            "files_referenced": list(refs or [spec]),
        },
    }


async def _analyze(ws: _FakeWorkspace, run: _StubRun) -> RepairContext:
    """Run the widening analyzer over the structural stubs above."""
    analyzer = SuiteRepairContextAnalyzer(
        RepairContextAnalyzer(workspace=cast("Any", ws)), workspace=cast("Any", ws)
    )
    return await analyzer.analyze(cast("Project", object()), cast("TestRun", run), persist=False)


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_config()
    yield
    reset_config()


# --------------------------------------------------------------------- pure path helpers


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a/b/todos.test.ts", "a/b/todos"),
        ("a/b/App.test.tsx", "a/b/App"),
        ("frontend/e2e/home.spec.ts", "frontend/e2e/home"),
        # The marker is stripped, not the rest of a dotted name.
        ("a/todos.controller.spec.ts", "a/todos.controller"),
        ("todos.test.ts", "todos"),
        ("a/b/todos.ts", None),  # not a test file at all
    ],
)
def test_the_stem_strips_only_the_test_marker(path: str, expected: str | None) -> None:
    assert spec_stem(path) == expected


def test_is_source_path_excludes_tests_and_non_source() -> None:
    assert is_source_path(CONTROLLER)
    assert not is_source_path(SPEC)
    assert not is_source_path(E2E)
    assert not is_source_path("backend/package.json")


def test_companions_put_the_same_stem_sibling_first() -> None:
    tree = [SPEC, CONTROLLER, SERVICE, SAME_STEM, ELSEWHERE]

    found = companion_sources(SPEC, tree)

    assert found[0] == SAME_STEM  # todos.test.ts tests todos.ts
    assert set(found) == {SAME_STEM, CONTROLLER, SERVICE}
    assert ELSEWHERE not in found  # a different feature is not a companion


def test_companions_never_reach_outside_the_test_directory() -> None:
    assert companion_sources(E2E, [E2E, PAGE, CONTROLLER]) == []


def test_suite_sources_stay_within_the_failing_half_of_the_app() -> None:
    tree = [CONTROLLER, PAGE, SAME_STEM]

    assert suite_sources([E2E], tree) == [PAGE]  # a frontend spec never reads the backend
    assert set(suite_sources([SPEC], tree)) == {CONTROLLER, SAME_STEM}


# --------------------------------------------------------------------- the analyzer


async def test_an_assertion_failure_still_yields_something_to_patch() -> None:
    """The headline regression: without widening this context is empty and Repair does nothing."""
    ws = _FakeWorkspace(files={SPEC: "expect(res.status).toBe(400)", CONTROLLER: "res.status(201)"})

    context = await _analyze(ws, _StubRun([_assertion_failure("rejects empty", SPEC)]))

    assert editable_paths(context) == [CONTROLLER]
    assert any("Widened the repair target set" in note for note in context.notes)


async def test_the_test_file_is_never_editable() -> None:
    """Widening must not hand the agent its own oracle — that is how a green run gets faked."""
    ws = _FakeWorkspace(files={SPEC: "spec", CONTROLLER: "source"})

    context = await _analyze(ws, _StubRun([_assertion_failure("rejects empty", SPEC)]))

    assert SPEC not in editable_paths(context)


async def test_every_failing_test_gets_a_target_not_just_the_ones_naming_a_source() -> None:
    """The "it didn't fix *all* of them" case.

    One failure names its source (so the run-level editable set is non-empty and the loop happily
    iterates); the other names only its spec. Judged per test, both get a target — judged over the
    run, the second could never be repaired however many iterations it was given.
    """
    other_spec = "backend/src/features/billing/invoices.test.ts"
    ws = _FakeWorkspace(
        files={SPEC: "spec", CONTROLLER: "source", other_spec: "spec", ELSEWHERE: "billing source"}
    )
    run = _StubRun(
        [
            _assertion_failure("names its source", SPEC, refs=[SPEC, CONTROLLER]),
            _assertion_failure("names only its spec", other_spec),
        ]
    )

    context = await _analyze(ws, run)

    assert set(editable_paths(context)) == {CONTROLLER, ELSEWHERE}


async def test_falls_back_to_what_changed_since_the_last_passing_run() -> None:
    ws = _FakeWorkspace(
        files={E2E: "spec", PAGE: "page"},
        last_passing="sha-old",
        current="sha-new",
        changed=[PAGE],
    )

    context = await _analyze(ws, _StubRun([_assertion_failure("home renders", E2E)]))

    assert editable_paths(context) == [PAGE]
    assert any("since the last passing run" in note for note in context.notes)


async def test_falls_back_to_the_suite_source_tree_when_there_is_no_green_history() -> None:
    """A first-ever run: no companion beside an e2e spec, and no last-passing ref to diff."""
    ws = _FakeWorkspace(files={E2E: "spec", PAGE: "page", CONTROLLER: "backend"})

    context = await _analyze(ws, _StubRun([_assertion_failure("home renders", E2E)]))

    assert editable_paths(context) == [PAGE]  # the frontend tree only — never the backend
    assert any("source tree" in note for note in context.notes)


async def test_a_context_that_already_names_its_source_is_left_alone() -> None:
    """Widening is additive and last-resort: a failure that already points at code is untouched."""
    ws = _FakeWorkspace(files={SPEC: "spec", CONTROLLER: "source", SERVICE: "service"})
    run = _StubRun([_assertion_failure("crashes", SPEC, refs=[CONTROLLER, SPEC])])

    context = await _analyze(ws, run)

    assert editable_paths(context) == [CONTROLLER]  # SERVICE was never pulled in
    assert not any("Widened" in note for note in context.notes)


async def test_a_green_run_is_not_widened() -> None:
    ws = _FakeWorkspace(files={SPEC: "spec", CONTROLLER: "source"})
    passing = _StubRun([{"name": "ok", "status": "passed", "framework": "jest", "file": SPEC}])

    context = await _analyze(ws, passing)

    assert context.target_files == []
    assert not any("Widened" in note for note in context.notes)


async def test_a_broken_workspace_listing_escalates_cleanly_instead_of_raising() -> None:
    ws = _FakeWorkspace(files={SPEC: "spec", CONTROLLER: "source"}, tree_fails=True)

    context = await _analyze(ws, _StubRun([_assertion_failure("rejects empty", SPEC)]))

    assert editable_paths(context) == []  # the loop reports "nothing safe to patch", not a 500


# --------------------------------------------------------------------- reachability (phase-63)


async def test_a_frontend_spec_reaches_the_component_it_renders_not_just_its_neighbours() -> None:
    """The reported failure: the agent was handed `main.tsx`, which renders no form at all.

    `frontend/src/App.test.tsx` has no same-stem sibling and its only folder neighbours are the
    app shell. The one piece of evidence naming the subject is the spec's own import.
    """
    ws = _FakeWorkspace(
        files={
            APP_SPEC: "import { HomePage } from './pages/HomePage'",
            MAIN: "import { router } from './routes'",
            ROUTES: "import { HomePage } from './pages/HomePage'",
            PAGE: "export function HomePage() { return <form aria-label='Add a new task' /> }",
        }
    )

    context = await _analyze(ws, _StubRun([_assertion_failure("one label", APP_SPEC)]))

    editable = editable_paths(context)
    assert PAGE in editable  # before phase-63 this was unreachable
    assert editable[0] == PAGE  # …and it outranks the shell files beside the spec
    assert any("import" in note for note in context.notes)


async def test_the_same_stem_sibling_still_outranks_an_import() -> None:
    """Backend behaviour is preserved: `todos.test.ts` still points at `todos.ts` first."""
    ws = _FakeWorkspace(
        files={
            SPEC: "import { create } from './todos.service'",
            SAME_STEM: "same stem",
            SERVICE: "service",
            CONTROLLER: "controller",
        }
    )

    context = await _analyze(ws, _StubRun([_assertion_failure("rejects empty", SPEC)]))

    # same-stem → direct import → the rest of the folder.
    assert editable_paths(context) == [SAME_STEM, SERVICE, CONTROLLER]


async def test_a_package_import_never_drags_node_modules_into_the_context() -> None:
    ws = _FakeWorkspace(
        files={
            APP_SPEC: "import { render } from '@testing-library/react'\nimport './pages/HomePage'",
            PAGE: "page",
            "node_modules/@testing-library/react/index.js": "vendor",
        }
    )

    context = await _analyze(ws, _StubRun([_assertion_failure("renders", APP_SPEC)]))

    assert all("node_modules" not in path for path in context.file_paths())


async def test_a_test_still_uncovered_after_the_first_tier_reaches_the_later_ones() -> None:
    """Aggravator to the reported bug: the tiers used to be gated on the *run* having nothing.

    One failure finds a companion, so the run-level editable set is non-empty. The other — an e2e
    spec with no sibling and no resolvable import — must still fall through to the suite-source
    tier, or it can never be repaired however many iterations it is given.
    """
    ws = _FakeWorkspace(files={SPEC: "spec", CONTROLLER: "source", E2E: "spec", PAGE: "page"})
    run = _StubRun([_assertion_failure("backend", SPEC), _assertion_failure("home renders", E2E)])

    context = await _analyze(ws, run)

    assert set(editable_paths(context)) == {CONTROLLER, PAGE}
    assert any("source tree" in note for note in context.notes)


async def test_the_import_walk_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rollback path in the phase plan: depth 0 returns pre-phase-63 behaviour."""
    monkeypatch.setenv("REPAIR_IMPORT_MAX_DEPTH", "0")
    reset_config()
    ws = _FakeWorkspace(
        files={APP_SPEC: "import { HomePage } from './pages/HomePage'", MAIN: "shell", PAGE: "page"}
    )

    context = await _analyze(ws, _StubRun([_assertion_failure("one label", APP_SPEC)]))

    assert editable_paths(context) == [MAIN]  # the old, wrong answer — proving the knob works
