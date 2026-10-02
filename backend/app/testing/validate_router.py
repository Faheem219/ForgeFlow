"""Validation read API (phase-40) — what the Validate panel hydrates from.

- ``GET /projects/{id}/validate/latest`` — the persisted validation report (outcome, the
  repair→redeploy→re-validate timeline, the final live URL, the escalation), or ``null`` before the
  first run.
- ``GET /projects/{id}/validate/live-run`` — the newest ``env=live`` test run, for per-test results.

Running validation is *not* here: it goes through the conductor (``POST /projects/{id}/intent`` with
``stage=validate``), so the validate⇐deploy hard prereq (§8) stays enforced in one place.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_id
from app.db.models.enums import ArtifactType, Stage, TestEnv
from app.db.repos import StageStateRepo, TestRunRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.stages.validate import VALIDATION_REPORT_KIND
from app.projects.service import ProjectService, parse_object_id
from app.testing.models import TestResult

validate_router = APIRouter(prefix="/projects", tags=["validate"])


async def _current(project_id: PydanticObjectId, created_at: datetime) -> bool:
    """Whether a validation result post-dates the last blank-slate reset of the stage.

    A reset validate stage (or a reset deploy, which takes validate with it) must read as never
    validated: the earlier report described a deployment that may no longer exist. The records are
    kept; they just stop being the stage's current result.
    """
    cleared_at = await StageStateRepo().cleared_at(project_id, Stage.validate)
    return cleared_at is None or _utc(created_at) > _utc(cleared_at)


def _utc(value: datetime) -> datetime:
    # The meta client is tz-aware (app/db/mongo.py); a naive value can only be UTC from BSON.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ValidationReportPublic(BaseModel):
    outcome: str
    url: str | None = None
    cycles: list[dict[str, Any]] = Field(default_factory=list)
    live_run_id: str | None = None
    reason: str | None = None
    summary: str = ""
    failing_tests: list[dict[str, Any]] = Field(default_factory=list)
    repair_escalation: dict[str, Any] | None = None
    wall_clock_s: float = 0.0


class LiveRunPublic(BaseModel):
    """The newest live run's per-test results — the "does prod actually work" table."""

    id: str
    total: int
    passed: int
    failed: int
    green: bool
    created_at: str
    results: list[TestResult] = Field(default_factory=list)


@validate_router.get("/{project_id}/validate/latest", response_model=ValidationReportPublic | None)
async def latest_validation(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> ValidationReportPublic | None:
    """The most recent validation report, or ``null`` when validation has never run."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)

    artifacts = ArtifactService()
    latest = await artifacts.get_latest(pid, Stage.validate, ArtifactType.test_result)
    if latest is None or latest.meta.get("kind") != VALIDATION_REPORT_KIND:
        return None
    if not await _current(pid, latest.created_at):
        return None
    content = await artifacts.get_content(latest)
    if not content:
        return None
    try:
        return ValidationReportPublic.model_validate(json.loads(content))
    except (ValueError, TypeError):  # a corrupt report must not 500 the panel
        return None


@validate_router.get("/{project_id}/validate/live-run", response_model=LiveRunPublic | None)
async def latest_live_run(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> LiveRunPublic | None:
    """The newest run made against the deployed URL (never a sandbox run)."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)

    run = await TestRunRepo().latest(pid, env=TestEnv.live)
    if run is None or not await _current(pid, run.created_at):
        return None

    results = [TestResult.model_validate(r) for r in run.results]
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    return LiveRunPublic(
        id=str(run.id),
        total=len(results),
        passed=passed,
        failed=failed,
        green=failed == 0 and passed > 0,
        created_at=run.created_at.isoformat(),
        results=results,
    )
