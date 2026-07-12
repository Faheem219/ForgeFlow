from __future__ import annotations

from datetime import datetime
from typing import ClassVar

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
