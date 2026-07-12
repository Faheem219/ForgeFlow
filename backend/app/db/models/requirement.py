from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel

from app.db.models.common import utcnow


class Feature(BaseModel):
    """A fill-in-the-blanks feature. The structured schema is formalized in phase-25."""

    name: str
    description: str = ""
    inputs: list[str] = Field(default_factory=list)
    expected_behaviors: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class RequirementSpec(Document):
    project_id: PydanticObjectId
    features: list[Feature] = Field(default_factory=list)
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "requirement_specs"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("project_id", pymongo.ASCENDING), ("version", pymongo.ASCENDING)],
                name="req_project_version",
            ),
        ]
