from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import CriterionKind


class AcceptanceCriterion(BaseModel):
    """A single, testable acceptance criterion.

    ``id`` is the **stable join key** across requirements → tests (phase-27) → results (phase-28) →
    the repair loop's minimal context (phase-29). It is assigned by the requirements service and
    preserved across edits where the criterion is unchanged.
    """

    id: str
    text: str
    kind: CriterionKind = CriterionKind.either


class Feature(BaseModel):
    """A fill-in-the-blanks feature (structured schema formalized in phase-25)."""

    name: str
    description: str = ""
    inputs: list[str] = Field(default_factory=list)
    expected_behaviors: list[str] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)


class RequirementSpec(Document):
    project_id: PydanticObjectId
    #: A short product name for the app, proposed by the requirements draft and freely editable.
    #: Deliberately *not* a :class:`Feature`: it has nothing testable about it, so it would only
    #: pollute the spec (and the generated test suite) as a criterion-less feature. Blank is fine —
    #: consumers fall back to the ForgeFlow project's own name.
    app_name: str = ""
    features: list[Feature] = Field(default_factory=list)
    version: int = 1
    # True when the test-gen agent inferred this spec from design/build artifacts because the
    # requirements stage was skipped (phase-27, D12) — surfaced so it is never mistaken for a
    # human-authored spec.
    inferred: bool = False
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "requirement_specs"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("project_id", pymongo.ASCENDING), ("version", pymongo.ASCENDING)],
                name="req_project_version",
            ),
        ]
