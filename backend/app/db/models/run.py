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


class Run(Document):
    """A costed, traced unit of agent work (observability + eval; phase-46/44)."""

    project_id: PydanticObjectId | None = None
    kind: str
    cost: Cost = Field(default_factory=Cost)
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
