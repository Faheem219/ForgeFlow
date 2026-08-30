"""Repair API (phase-32 wiring) — drive and read back the bounded repair loop.

The loop itself is phase-31; this is the thin HTTP surface the Test-stage UI needs:

- ``POST /projects/{id}/repair/run`` — run the bounded loop over a failing ``TestRun`` (the latest
  by default). Iterations stream as ``repair.iteration`` events while the request is in flight, and
  the terminal ``repair.done``/``repair.escalation`` arrives with the response.
- ``GET  /projects/{id}/repair/latest`` — the persisted loop report (so a reload doesn't lose the
  escalation payload).
- ``GET  /projects/{id}/repair/attempts/{attempt_id}/diff`` — one attempt's patch, for the diff
  view.

Ownership is enforced through the owning project; a record the caller doesn't own is a ``404``, so
its existence is never leaked.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.agents.suite_context import SuiteRepairContextAnalyzer
from app.auth.deps import get_current_user_id
from app.core.errors import NotFoundError, UserError
from app.core.limits import expensive_rate_limit
from app.db.blobs import get_blob_store
from app.db.models import RepairAttempt
from app.db.models.enums import ArtifactType, Stage, TestEnv
from app.db.repos import RepairAttemptRepo, TestRunRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.stages.repair import REPAIR_REPORT_KIND, RepairLoopController
from app.projects.service import ProjectService, parse_object_id

repair_router = APIRouter(prefix="/projects", tags=["repair"])

# In-process registry of cancellable loops, keyed by project. Single-instance (same caveat as the
# realtime hub); to scale, move this behind the same pub/sub the hub would use. The controller
# checks the flag between iterations, so cancelling takes effect at the next iteration boundary —
# never mid-patch, which keeps the workspace consistent.
_active_loops: dict[str, asyncio.Event] = {}


class RunRepairRequest(BaseModel):
    """Which failing run to repair; omit to use the project's most recent run."""

    test_run_id: str | None = None


class RepairLoopPublic(BaseModel):
    outcome: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    final_run_id: str | None = None
    escalation: dict[str, Any] | None = None


class AttemptDiffPublic(BaseModel):
    attempt_id: str
    iteration: int
    diff: str


class CancelRepairPublic(BaseModel):
    """``cancelling=False`` means there was no loop running to stop."""

    cancelling: bool


@repair_router.post(
    "/{project_id}/repair/run",
    response_model=RepairLoopPublic,
    dependencies=[Depends(expensive_rate_limit)],
)
async def run_repair(
    project_id: str,
    body: RunRepairRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> RepairLoopPublic:
    """Run the bounded repair loop; progress streams over the realtime channel."""
    pid = parse_object_id(project_id)
    project = await ProjectService().get_owned(pid, user_id)

    runs = TestRunRepo()
    if body.test_run_id:
        test_run = await runs.get(parse_object_id(body.test_run_id, "Test run"))
        if test_run is None or test_run.project_id != pid:
            raise NotFoundError("Test run not found")
    else:
        # Explicitly the *sandbox* run: live runs (phase-39) share this collection, and the repair
        # loop patches workspace code. Repairing from a live result is phase-40's path.
        test_run = await runs.latest(pid, env=TestEnv.sandbox)
        if test_run is None:
            raise UserError("Run the tests first — there is no test run to repair.")

    cancel = asyncio.Event()
    _active_loops[str(pid)] = cancel
    try:
        # The widening analyzer, not the bare one: a plain assertion failure names only its own
        # spec in the stack, and the spec is read-only during repair — so the bare context leaves
        # nothing editable and the loop escalates without changing a line. The widener resolves
        # what each failing test actually exercises, so pressing Repair patches code.
        controller = RepairLoopController(analyzer=SuiteRepairContextAnalyzer())
        result = await controller.run(project, test_run, cancel=cancel)
    finally:
        # Only retract our own registration: if a second loop for this project has since taken
        # the slot, popping it blindly would leave that one with no way to be cancelled.
        if _active_loops.get(str(pid)) is cancel:
            _active_loops.pop(str(pid), None)
    return RepairLoopPublic.model_validate(result.to_dict())


@repair_router.post("/{project_id}/repair/cancel", response_model=CancelRepairPublic)
async def cancel_repair(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> CancelRepairPublic:
    """Ask a running loop to stop at the next iteration boundary."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)
    event = _active_loops.get(str(pid))
    if event is not None:
        event.set()
    return CancelRepairPublic(cancelling=event is not None)


@repair_router.get("/{project_id}/repair/latest", response_model=RepairLoopPublic | None)
async def latest_repair(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> RepairLoopPublic | None:
    """The most recent loop report, or ``null`` when repair has never run."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)

    artifacts = ArtifactService()
    latest = await artifacts.get_latest(pid, Stage.test, ArtifactType.repair_attempt)
    if latest is None or latest.meta.get("kind") != REPAIR_REPORT_KIND:
        return None
    content = await artifacts.get_content(latest)
    if not content:
        return None
    try:
        return RepairLoopPublic.model_validate(json.loads(content))
    except (ValueError, TypeError):  # a corrupt report must not 500 the panel
        return None


@repair_router.get(
    "/{project_id}/repair/attempts/{attempt_id}/diff", response_model=AttemptDiffPublic
)
async def attempt_diff(
    project_id: str,
    attempt_id: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> AttemptDiffPublic:
    """The unified diff one repair attempt applied."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)

    attempt: RepairAttempt | None = await RepairAttemptRepo().get(
        parse_object_id(attempt_id, "Repair attempt")
    )
    if attempt is None or attempt.project_id != pid:
        raise NotFoundError("Repair attempt not found")

    diff = ""
    if attempt.diff_ref:
        try:
            diff = (await get_blob_store().get(attempt.diff_ref)).decode("utf-8")
        except Exception:  # a missing blob is an empty diff, not an error
            diff = ""
    return AttemptDiffPublic(attempt_id=str(attempt.id), iteration=attempt.iteration, diff=diff)
