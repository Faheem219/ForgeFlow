from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import Stage, StageStatus


class StageState(Document):
    """One row per (project, stage) — the substrate of the non-linear state machine (phase-06)."""

    project_id: PydanticObjectId
    stage: Stage
    status: StageStatus = StageStatus.empty
    #: The status this stage held when it was skipped — what `unskip` restores it to. Written only
    #: by a `skip`; every other status write clears it. `None` on rows predating restore points.
    previous_status: StageStatus | None = None
    #: When a ``reset`` last returned this stage to a blank slate (deploy, validate). Anything the
    #: stage produced before it — a deployment, a validation report, a live test run — is history:
    #: kept, but no longer shown as the stage's current result. ``None`` until the first such reset,
    #: and left alone by every status write, so the cut-off survives the stage running again.
    cleared_at: datetime | None = None
    artifacts: list[PydanticObjectId] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "stage_states"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("project_id", pymongo.ASCENDING), ("stage", pymongo.ASCENDING)],
                unique=True,
                name="uniq_project_stage",
            ),
        ]
