from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel

from app.db.models.common import utcnow


class Cost(BaseModel):
    tokens: int = 0
    inr: float = 0.0
    # phase-64: reasoning tokens counted inside `tokens` (defaulted so older Runs load unchanged).
    reasoning_tokens: int = 0


class RunProgress(BaseModel):
    """Live, *durable* progress for work in flight.

    Realtime events alone cannot answer "what is this run doing right now?" after a page reload:
    the hub's replay ring is bounded and a build's agent tokens evict it many times over. Persisting
    the same signal here makes a refresh (or a second viewer) able to reattach to a running build
    and see the step it is on and everything it has written so far.
    """

    # The coarse step: instantiate_skeleton | survey | plan | phase | implement | verify | repair |
    # report | failed. (`phase` is phase-56's per-phase implement step; `implement` remains for
    # runs recorded before it, and for the unphased path.)
    step: str = ""
    label: str = ""  # human sentence for the current action ("Installing dependencies")
    target: str = ""  # the file/command the action concerns, when meaningful
    files: list[str] = Field(default_factory=list)  # written this run, in order
    # Which build phase is in flight (phase-56). All defaulted, so Run documents written before
    # phasing deserialize unchanged; a reloaded page reattaches to the right phase from here rather
    # than from the bounded realtime ring.
    phase_index: int = 0  # 1-based; 0 means "no phase in flight"
    phase_total: int = 0
    phase_id: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class Run(Document):
    """A costed, traced unit of agent work (observability + eval; phase-46/44)."""

    project_id: PydanticObjectId | None = None
    kind: str
    cost: Cost = Field(default_factory=Cost)
    # Live progress for a run still in flight; read by the reattach endpoint after a refresh.
    progress: RunProgress = Field(default_factory=RunProgress)
    # One entry per agent tool call (phase-21): {tool, ok, at}. The auditable action trail.
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    # Terminal result of the work this Run bounds — e.g. the repair loop's `fixed`/`escalated`
    # (phase-31). Left None by runs that have no meaningful terminal state. Read by the eval
    # harness (D13, phase-44), which treats one Run as one costed unit of work.
    outcome: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    events_ref: str | None = None

    class Settings:
        name = "runs"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("project_id", pymongo.ASCENDING), ("started_at", pymongo.DESCENDING)],
                name="run_project_started",
            ),
        ]
