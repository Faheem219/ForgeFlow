"""Testing API (phase-28): trigger a run and read structured results.

``POST /projects/{id}/tests/run`` executes the suites in the sandbox and returns the parsed run;
per-test + summary ``test.result`` events stream over the realtime channel meanwhile. Ownership is
enforced via the owning project (a run the caller doesn't own is a ``404`` — its existence is never
leaked).
"""

from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user_id
from app.core.errors import NotFoundError
from app.db.models import TestRun
from app.db.repos import TestRunRepo
from app.projects.service import ProjectService, parse_object_id
from app.testing.models import (
    RunTestsRequest,
    TestResult,
    TestRunPublic,
    TestRunSummaryPublic,
    summarize,
)
from app.testing.runner import TestRunner

tests_router = APIRouter(prefix="/projects", tags=["testing"])


def _results(run: TestRun) -> list[TestResult]:
    return [TestResult.model_validate(r) for r in run.results]


def _summary_public(run: TestRun) -> TestRunSummaryPublic:
    summary = summarize(_results(run))
    return TestRunSummaryPublic(
        id=str(run.id),
        total=summary.total,
        passed=summary.passed,
        failed=summary.failed,
        skipped=summary.skipped,
        green=summary.green,
        env=str(run.env),
        created_at=run.created_at.isoformat(),
    )


def _full_public(run: TestRun) -> TestRunPublic:
    results = _results(run)
    summary = summarize(results)
    return TestRunPublic(
        id=str(run.id),
        total=summary.total,
        passed=summary.passed,
        failed=summary.failed,
        skipped=summary.skipped,
        green=summary.green,
        env=str(run.env),
        created_at=run.created_at.isoformat(),
        results=results,
        failures=[r for r in results if r.status == "failed"],
        suite_refs=[str(ref) for ref in run.suite_refs],
        stdout_ref=run.stdout_ref,
    )


@tests_router.post("/{project_id}/tests/run", response_model=TestRunPublic)
async def run_tests(
    project_id: str,
    body: RunTestsRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> TestRunPublic:
    """Run the suites in the sandbox; per-test progress streams over the realtime channel."""
    project = await ProjectService().get_owned(parse_object_id(project_id), user_id)
    run = await TestRunner().run(project, scope=body.scope, name_filter=body.filter)
    return _full_public(run)


@tests_router.get("/{project_id}/tests/runs", response_model=list[TestRunSummaryPublic])
async def list_test_runs(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> list[TestRunSummaryPublic]:
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)  # 404 if missing / not owned
    runs = await TestRunRepo().list_for_project(pid)
    return [_summary_public(r) for r in runs]


@tests_router.get("/{project_id}/tests/runs/{run_id}", response_model=TestRunPublic)
async def get_test_run(
    project_id: str,
    run_id: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> TestRunPublic:
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)
    run = await TestRunRepo().get(parse_object_id(run_id, "Test run"))
    if run is None or run.project_id != pid:
        raise NotFoundError("Test run not found")
    return _full_public(run)
