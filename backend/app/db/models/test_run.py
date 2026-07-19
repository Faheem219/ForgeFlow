from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import TestEnv


class TestRun(Document):
    project_id: PydanticObjectId
    suite_refs: list[PydanticObjectId] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    stdout_ref: str | None = None
    # Blob holding the minimal repair context assembled from this run's failures (phase-29) —
    # the auditable input to the repair agent (phase-30) and the eval harness (phase-44).
    repair_context_ref: str | None = None
    env: TestEnv = TestEnv.sandbox  # sandbox preview vs live URL (phase-39)
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "test_runs"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("project_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)],
                name="run_project_created",
            ),
        ]
