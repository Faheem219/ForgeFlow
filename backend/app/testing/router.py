"""Testing API (phase-28): trigger a run and read structured results.

``POST /projects/{id}/tests/run`` executes the suites in the sandbox and returns the parsed run;
per-test + summary ``test.result`` events stream over the realtime channel meanwhile. Ownership is
enforced via the owning project (a run the caller doesn't own is a ``404`` — its existence is never
leaked).

``GET /projects/{id}/tests/runs`` lists **sandbox** runs by default. Live-validation runs (phase-39)
live in the same collection but cover only the E2E subset, so an unscoped listing would let the
newest live run masquerade as the Test stage's latest result.
"""

from __future__ import annotations

from typing import Literal

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, Query

from app.auth.deps import get_current_user_id
from app.core.errors import NotFoundError
from app.core.limits import expensive_rate_limit
from app.db.blobs import get_blob_store
from app.db.models import TestRun
from app.db.models.enums import TestEnv
from app.db.repos import TestRunRepo
from app.projects.service import ProjectService, parse_object_id
from app.testing.models import (
    RunTestsRequest,
    TestResult,
    TestRunPublic,
    TestRunSummaryPublic,
    TestStdoutPublic,
    summarize,
)
from app.testing.runner import TestRunner

tests_router = APIRouter(prefix="/projects", tags=["testing"])

#: Which environment's runs a listing wants. Sandbox and live runs (phase-39) share one collection,
#: and a live run covers only the E2E subset — so this endpoint, which backs the *Test stage*, is
#: scoped to the sandbox by default. Live results have their own read: ``GET /validate/live-run``.
TestEnvFilter = Literal["sandbox", "live", "all"]


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


@tests_router.post(
    "/{project_id}/tests/run",
    response_model=TestRunPublic,
    dependencies=[Depends(expensive_rate_limit)],
)
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
    project_id: str,
    env: TestEnvFilter = Query(
        default="sandbox", description="Environment to list; 'all' returns sandbox + live."
    ),
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> list[TestRunSummaryPublic]:
    """The project's test runs, newest first — sandbox runs unless ``env`` says otherwise."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)  # 404 if missing / not owned
    runs = await TestRunRepo().list_for_project(pid, env=None if env == "all" else TestEnv(env))
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


@tests_router.get("/{project_id}/tests/runs/{run_id}/stdout", response_model=TestStdoutPublic)
async def get_test_stdout(
    project_id: str,
    run_id: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> TestStdoutPublic:
    """The run's captured reporter output (phase-32's stdout viewer)."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)
    run = await TestRunRepo().get(parse_object_id(run_id, "Test run"))
    if run is None or run.project_id != pid:
        raise NotFoundError("Test run not found")

    stdout = ""
    if run.stdout_ref:
        try:
            stdout = (await get_blob_store().get(run.stdout_ref)).decode("utf-8")
        except Exception:  # a missing blob is empty output, not an error
            stdout = ""
    return TestStdoutPublic(id=str(run.id), stdout=stdout)
