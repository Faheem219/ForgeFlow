"""Evaluation API (phase-45) — read-only access to the harness's measurements.

- ``GET /eval/summary`` — aggregates for the dashboard (both pass rates, distributions, outcomes)
- ``GET /eval/runs`` — the run reports on disk, newest first (metadata only)
- ``GET /eval/report.csv`` — the stable export schema
- ``GET /eval/report.md`` — a report suited to a paper appendix or a pitch

Nothing here runs the pipeline or mutates a record — running is phase-44's CLI, deliberately kept
out of the API so an HTTP request can never start a billable corpus run. Any authenticated user may
read the evidence; the admin area (phase-52) surfaces the same endpoints.
"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_id
from app.eval.report import latest_records, load_reports, summarize, to_csv, to_markdown

eval_router = APIRouter(prefix="/eval", tags=["eval"])


class RunSummaryPublic(BaseModel):
    """One report file's headline — enough to pick which run to look at."""

    source: str
    started_at: str | None = None
    finished_at: str | None = None
    legs: list[str] = Field(default_factory=list)
    specs: int = 0


class EvalSummaryPublic(BaseModel):
    """Everything the dashboard charts. ``records`` is the per-spec table."""

    available: bool
    summary: dict[str, Any] = Field(default_factory=dict)
    records: list[dict[str, Any]] = Field(default_factory=list)
    source: str | None = None


def _records(source: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """The chosen run's records (default: the newest)."""
    if source is None:
        reports = load_reports()
        if not reports:
            return [], None
        return list(reports[0]["records"]), str(reports[0].get("source"))

    for report in load_reports():
        if report.get("source") == source:
            return list(report["records"]), source
    return [], None


@eval_router.get("/summary", response_model=EvalSummaryPublic)
async def eval_summary(
    source: str | None = Query(default=None, description="report file name; default the newest"),
    _user_id: PydanticObjectId = Depends(get_current_user_id),
) -> EvalSummaryPublic:
    """Aggregates + per-spec records. ``available=false`` before the harness has ever run."""
    records, chosen = _records(source)
    if not records:
        return EvalSummaryPublic(available=False)
    return EvalSummaryPublic(
        available=True, summary=summarize(records), records=records, source=chosen
    )


@eval_router.get("/runs", response_model=list[RunSummaryPublic])
async def eval_runs(
    _user_id: PydanticObjectId = Depends(get_current_user_id),
) -> list[RunSummaryPublic]:
    """Every run report on disk, newest first."""
    return [
        RunSummaryPublic(
            source=str(report.get("source", "")),
            started_at=report.get("started_at"),
            finished_at=report.get("finished_at"),
            legs=list(report.get("legs") or []),
            specs=len(report.get("records") or []),
        )
        for report in load_reports()
    ]


@eval_router.get("/report.csv", response_class=PlainTextResponse)
async def eval_report_csv(
    source: str | None = Query(default=None),
    _user_id: PydanticObjectId = Depends(get_current_user_id),
) -> PlainTextResponse:
    records, _chosen = _records(source)
    return PlainTextResponse(
        to_csv(records),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="forgeflow-eval.csv"'},
    )


@eval_router.get("/report.md", response_class=PlainTextResponse)
async def eval_report_markdown(
    source: str | None = Query(default=None),
    _user_id: PydanticObjectId = Depends(get_current_user_id),
) -> PlainTextResponse:
    records, _chosen = _records(source)
    return PlainTextResponse(
        to_markdown(records),
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="forgeflow-eval.md"'},
    )


__all__ = ["eval_router", "latest_records"]
