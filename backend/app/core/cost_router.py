"""Cost & run-trace API (phase-46, §7/D4) — spend made visible rather than silently enforced.

- ``GET /projects/{id}/cost`` — this project's spend, its budget headroom, and a per-kind breakdown
- ``GET /projects/{id}/runs`` — the run explorer: kind, cost, outcome, duration, newest first
- ``GET /admin/cost``        — global spend across every project (admin only)

There is no new accounting here: every number is summed from the ``Run`` records phase-20 already
writes, so the dashboard, the budget guard and the eval harness cannot disagree about spend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.agents.budget import budget_snapshot
from app.agents.cost import global_spend_inr
from app.auth.deps import get_current_user_id, require_admin
from app.core.config import get_config
from app.db.models import Project, Run, User
from app.db.repos import RunRepo
from app.projects.service import ProjectService, parse_object_id

cost_router = APIRouter(tags=["cost"])


class RunPublic(BaseModel):
    """One costed unit of work. ``duration_s`` is ``None`` while a run is still open."""

    id: str
    kind: str
    tokens: int
    inr: float
    outcome: str | None = None
    tool_calls: int = 0
    started_at: datetime
    finished_at: datetime | None = None
    duration_s: float | None = None


class BudgetPublic(BaseModel):
    """Caps and what is left. ``None`` cap means unlimited — headroom is then ``None``, not 0."""

    spent_inr: float
    cap_inr: float | None = None
    headroom_inr: float | None = None
    used_ratio: float | None = None
    warn_ratio: float
    warning: bool = False
    halted: bool = False


class ProjectCostPublic(BaseModel):
    project_id: str
    tokens: int
    inr: float
    runs: int
    by_kind: dict[str, dict[str, float]] = Field(default_factory=dict)
    project: BudgetPublic
    global_budget: BudgetPublic


class GlobalCostPublic(BaseModel):
    inr: float
    tokens: int
    runs: int
    projects: int
    top_projects: list[dict[str, Any]] = Field(default_factory=list)
    budget: BudgetPublic


def _duration(run: Run) -> float | None:
    if run.finished_at is None:
        return None
    return round((run.finished_at - run.started_at).total_seconds(), 3)


def _run_public(run: Run) -> RunPublic:
    return RunPublic(
        id=str(run.id),
        kind=run.kind,
        tokens=run.cost.tokens,
        inr=round(run.cost.inr, 6),
        outcome=run.outcome,
        tool_calls=len(run.tool_calls),
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_s=_duration(run),
    )


def _budget(spent: float, cap: float | None, warn_ratio: float) -> BudgetPublic:
    capped = cap is not None and cap > 0
    return BudgetPublic(
        spent_inr=round(spent, 6),
        cap_inr=cap,
        # No cap means unlimited: headroom is unknown, not zero.
        headroom_inr=None if cap is None else round(cap - spent, 6),
        used_ratio=round(spent / cap, 4) if capped and cap else None,
        warn_ratio=warn_ratio,
        warning=(capped and cap is not None and warn_ratio > 0 and cap * warn_ratio <= spent < cap),
        halted=capped and cap is not None and spent >= cap,
    )


@cost_router.get("/projects/{project_id}/cost", response_model=ProjectCostPublic)
async def project_cost(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> ProjectCostPublic:
    """Spend + headroom for one project, broken down by the kind of work that caused it."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)

    runs = await RunRepo().list_for_project(pid)
    snapshot = await budget_snapshot(pid)
    warn_ratio = float(get_config().get("budget_warn_ratio"))

    by_kind: dict[str, dict[str, float]] = {}
    for run in runs:
        bucket = by_kind.setdefault(run.kind, {"runs": 0, "tokens": 0, "inr": 0.0})
        bucket["runs"] += 1
        bucket["tokens"] += run.cost.tokens
        bucket["inr"] = round(bucket["inr"] + run.cost.inr, 6)

    return ProjectCostPublic(
        project_id=project_id,
        tokens=sum(r.cost.tokens for r in runs),
        inr=round(sum(r.cost.inr for r in runs), 6),
        runs=len(runs),
        by_kind=by_kind,
        project=_budget(snapshot.project_inr, snapshot.project_cap, warn_ratio),
        global_budget=_budget(snapshot.global_inr, snapshot.global_cap, warn_ratio),
    )


@cost_router.get("/projects/{project_id}/runs", response_model=list[RunPublic])
async def project_runs(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> list[RunPublic]:
    """The run explorer: every costed unit of work on this project, newest first."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)
    return [_run_public(run) for run in await RunRepo().list_for_project(pid)]


@cost_router.get("/admin/cost", response_model=GlobalCostPublic)
async def admin_cost(_admin: User = Depends(require_admin)) -> GlobalCostPublic:
    """Platform-wide spend. Admin only — one user must never see another's costs."""
    runs = await Run.find_all().to_list()
    config = get_config()
    cap = config.get("budget_cap_inr_global")

    per_project: dict[str, dict[str, float]] = {}
    for run in runs:
        if run.project_id is None:
            continue
        bucket = per_project.setdefault(str(run.project_id), {"inr": 0.0, "tokens": 0, "runs": 0})
        bucket["inr"] = round(bucket["inr"] + run.cost.inr, 6)
        bucket["tokens"] += run.cost.tokens
        bucket["runs"] += 1

    names = {str(p.id): p.name for p in await Project.find_all().to_list() if p.id is not None}
    top = sorted(per_project.items(), key=lambda kv: kv[1]["inr"], reverse=True)[:10]

    return GlobalCostPublic(
        inr=round(await global_spend_inr(), 6),
        tokens=sum(r.cost.tokens for r in runs),
        runs=len(runs),
        projects=len(per_project),
        top_projects=[
            {"project_id": pid, "name": names.get(pid, "(deleted)"), **stats} for pid, stats in top
        ],
        budget=_budget(
            await global_spend_inr(),
            None if cap in (None, "") else float(cap),
            float(config.get("budget_warn_ratio")),
        ),
    )
