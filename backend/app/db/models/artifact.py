from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import ArtifactType, Stage


class Artifact(Document):
    """A versioned stage output. Never overwritten — new versions are appended (phase-07)."""

    project_id: PydanticObjectId
    stage: Stage
    type: ArtifactType  # noqa: A003 - domain field name from §6
    version: int = 1
    ref: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "artifacts"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [
                    ("project_id", pymongo.ASCENDING),
                    ("stage", pymongo.ASCENDING),
                    ("type", pymongo.ASCENDING),
                    ("version", pymongo.ASCENDING),
                ],
                name="artifact_lookup",
            ),
        ]
