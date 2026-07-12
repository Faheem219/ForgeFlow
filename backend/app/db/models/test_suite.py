from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import TestKind


class TestSuite(Document):
    project_id: PydanticObjectId
    kind: TestKind
    files: list[str] = Field(default_factory=list)
    generated_from: list[str] = Field(default_factory=list)  # requirement criterion ids
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "test_suites"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("project_id", pymongo.ASCENDING), ("version", pymongo.ASCENDING)],
                name="suite_project_version",
            ),
        ]
