from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import RepairOutcome


class RepairAttempt(Document):
    """One diff-aware patch iteration in the bounded repair loop (phase-30/31)."""

    project_id: PydanticObjectId
    run_id: PydanticObjectId | None = None
    iteration: int
    target_files: list[str] = Field(default_factory=list)
    diff_ref: str | None = None
    resulting_run_id: PydanticObjectId | None = None
    outcome: RepairOutcome | None = None
    #: Set when the loop undid this patch because it was strictly worse — it broke a passing test
    #: and fixed nothing. The attempt stays in the trail (its diff is still readable); the flag
    #: records that the code it contains is *not* what the workspace ended up with.
    reverted: bool = False
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "repair_attempts"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("project_id", pymongo.ASCENDING), ("created_at", pymongo.ASCENDING)],
                name="repair_project_created",
            ),
        ]
