"""Shared fakes for the bounded repair-loop controller tests (phase-31).

Not a test module. The controller drives two seams — a context analyzer and a patch agent — so a
whole loop (converge / stall / regress / cap / escalate) is scripted deterministically without a
model, a sandbox, or a test runner.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.agents.repair import RepairResult
from app.agents.repair_context import ContextFile, FailingTest, RepairContext
from app.core.config import reset_config
from app.db.models import Project, RepairAttempt, Run, TestRun
from app.testing.models import Failure, TestResult, TestStatus

SRC = "backend/src/features/todos/todos.controller.ts"
TEST_FILE = "backend/src/features/todos/todos.test.ts"


def configure_loop(
    monkeypatch: pytest.MonkeyPatch,
    *,
    max_iterations: int | None = None,
    stall_threshold: int | None = None,
    max_noop: int | None = None,
) -> None:
    """Override the config-resolved loop bounds (admin > env > default)."""
    if max_iterations is not None:
        monkeypatch.setenv("REPAIR_MAX_ITERATIONS", str(max_iterations))
    if stall_threshold is not None:
        monkeypatch.setenv("REPAIR_STALL_THRESHOLD", str(stall_threshold))
    if max_noop is not None:
        monkeypatch.setenv("REPAIR_MAX_NOOP_ATTEMPTS", str(max_noop))
    reset_config()


def failing(name: str) -> TestResult:
    return TestResult(
        name=name,
        status=TestStatus.failed,
        framework="jest",
        file=TEST_FILE,
        failure=Failure(
            message="boom", assertion=name, stack=f"at {SRC}:1", files_referenced=[SRC]
        ),
    )


def passing(name: str) -> TestResult:
    return TestResult(name=name, status=TestStatus.passed, framework="jest", file=TEST_FILE)


def key(name: str) -> str:
    return f"{TEST_FILE}::{name}"


async def make_project(name: str = "app") -> Project:
    return await Project(user_id=PydanticObjectId(), name=name, app_db_name="db").insert()


async def make_test_run(project: Project, results: list[TestResult]) -> TestRun:
    assert project.id is not None
    return await TestRun(
        project_id=project.id,
        results=[r.model_dump(mode="json") for r in results],
        failures=[r.model_dump(mode="json") for r in results if r.status is TestStatus.failed],
    ).insert()


async def make_attempt(
    project: Project,
    run: Run,
    iteration: int,
    *,
    resulting_run: TestRun | None = None,
    target_files: list[str] | None = None,
) -> RepairAttempt:
    assert project.id is not None
    return await RepairAttempt(
        project_id=project.id,
        run_id=run.id,
        iteration=iteration,
        target_files=[SRC] if target_files is None else target_files,
        diff_ref=f"fs:diff-{iteration}",
        # The real agent links the attempt to the run it produced; the fake must too, or the
        # audit trail it stands in for is only half there.
        resulting_run_id=resulting_run.id if resulting_run else None,
    ).insert()


async def scripted_result(
    project: Project,
    run: Run,
    iteration: int,
    *,
    failing_names: list[str],
    passing_names: list[str] | None = None,
    newly_failing: list[str] | None = None,
    newly_passing: list[str] | None = None,
    files_written: list[str] | None = None,
    summary: str | None = None,
) -> RepairResult:
    """A RepairResult whose re-run leaves exactly ``failing_names`` red.

    ``files_written=[]`` models the phase-63 no-op attempt: the agent had something it *could*
    patch and wrote nothing, because the fix lay outside the context it was given.
    """
    results = [failing(n) for n in failing_names] + [passing(n) for n in (passing_names or [])]
    after = await make_test_run(project, results)
    written = [SRC] if files_written is None else files_written
    attempt = await make_attempt(project, run, iteration, resulting_run=after, target_files=written)
    return RepairResult(
        attempt=attempt,
        test_run=after,
        newly_failing=[key(n) for n in (newly_failing or [])],
        newly_passing=[key(n) for n in (newly_passing or [])],
        files_written=written,
        commit=f"sha{iteration}",
        # The pre-attempt anchor the loop resets to when a patch turns out to be pure damage.
        before_sha=f"sha{iteration - 1}",
        summary=summary if summary is not None else f"attempt {iteration}",
        green=not failing_names,
    )


class FakeRevertableWorkspace:
    """Records the restores the loop asked for; ``fails=True`` makes the undo itself fail."""

    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.restored: list[str] = []

    async def restore(self, project: Project, sha: str) -> bool:
        self.restored.append(sha)
        return not self.fails


class StubAnalyzer:
    """Returns a canned minimal context; records how many times it was asked."""

    def __init__(self) -> None:
        self.calls = 0

    async def analyze(
        self, project: Project, test_run: TestRun, *, persist: bool = True
    ) -> RepairContext:
        self.calls += 1
        return RepairContext(
            failing_tests=[
                FailingTest(
                    name="t",
                    framework="jest",
                    criterion_id="ac-1",
                    file=TEST_FILE,
                    message="boom",
                    assertion=None,
                    stack=None,
                    files_referenced=[SRC],
                )
            ],
            target_files=[ContextFile(path=SRC, content="source")],
        )


class ScriptedAgent:
    """Replays queued RepairResults; the last one repeats if the loop keeps going."""

    def __init__(self, results: list[RepairResult]) -> None:
        self._results = list(results)
        self.iterations: list[int] = []

    async def run(
        self,
        project: Project,
        run: Run,
        test_run: TestRun,
        *,
        context: RepairContext | None = None,
        ctx: object | None = None,
        channel: str | None = None,
        iteration: int = 1,
    ) -> RepairResult:
        self.iterations.append(iteration)
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]


class RaisingAgent:
    """Raises on the first call — models a budget halt mid-loop."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    async def run(self, *args: object, **kwargs: object) -> RepairResult:
        self.calls += 1
        raise self._error
